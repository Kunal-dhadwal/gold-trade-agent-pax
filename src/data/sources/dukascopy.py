"""Dukascopy public historical market data connector for Spot Gold (XAUUSD).

Primary free historical research data source providing institutional-grade
tick and minute bar data.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import lzma
from pathlib import Path
import struct
from typing import Any, Callable, Dict, List, Optional, Tuple
import httpx
import numpy as np
import pandas as pd
from src.core.config import settings
from src.core.logger import logger
from src.data.models import validate_candle_dataframe
from src.data.storage.parquet_manager import ParquetStorageManager
from src.data.validation.quality_engine import DataQualityEngine, QualityReport


class DukascopyClient:
    """Historical data client for Dukascopy Free Public Data Feed.

    Downloads LZMA-compressed binary tick data (.bi5), unpacks 20-byte records,
    aggregates into canonical OHLCV bars, and persists raw files and Parquet.
    """

    DEFAULT_BASE_URL = "http://datafeed.dukascopy.com/datafeed"
    POINT_DIVIDER_MAP = {
        "XAUUSD": 1000.0,
        "EURUSD": 100000.0,
        "GBPUSD": 100000.0,
        "USDJPY": 1000.0,
    }

    TIMEFRAME_RESAMPLE_MAP = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1D",
    }

    def __init__(
        self,
        base_url: Optional[str] = None,
        raw_storage_dir: Optional[Path] = None,
        max_retries: int = 3,
        backoff_factor: float = 1.5,
        request_delay: float = 0.05,
    ):
        self.base_url = (base_url or getattr(settings, "DUKASCOPY_BASE_URL", self.DEFAULT_BASE_URL)).rstrip("/")
        self.raw_dir = raw_storage_dir or (settings.RAW_DATA_DIR / "dukascopy")
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.request_delay = request_delay
        self.checkpoints_dir = settings.DATA_DIR / "metadata" / "checkpoints"
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

    def _get_point_divider(self, symbol: str) -> float:
        return self.POINT_DIVIDER_MAP.get(symbol.upper(), 1000.0)

    def _build_hour_url(self, symbol: str, dt_utc: datetime) -> str:
        # Dukascopy uses 0-indexed months (00 = January, 11 = December)
        year = dt_utc.year
        month_idx = dt_utc.month - 1
        day = dt_utc.day
        hour = dt_utc.hour
        return f"{self.base_url}/{symbol.upper()}/{year}/{month_idx:02d}/{day:02d}/{hour:02d}h_ticks.bi5"

    def _get_raw_file_path(self, symbol: str, dt_utc: datetime) -> Path:
        year = dt_utc.year
        month_idx = dt_utc.month - 1
        day = dt_utc.day
        hour = dt_utc.hour
        dest_dir = self.raw_dir / symbol.upper() / str(year) / f"{month_idx:02d}" / f"{day:02d}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        return dest_dir / f"{hour:02d}h_ticks.bi5"

    def parse_bi5_ticks(self, raw_bytes: bytes, symbol: str, base_hour: datetime) -> pd.DataFrame:
        """Decompress LZMA bi5 bytes and unpack into DataFrame of ticks."""
        if not raw_bytes:
            return pd.DataFrame(columns=["timestamp", "bid", "ask", "price", "volume"])

        try:
            decomp = lzma.decompress(raw_bytes)
        except Exception as e:
            logger.warning(f"Failed to decompress LZMA bi5 payload: {e}")
            return pd.DataFrame(columns=["timestamp", "bid", "ask", "price", "volume"])

        record_size = 20
        num_ticks = len(decomp) // record_size
        if num_ticks == 0:
            return pd.DataFrame(columns=["timestamp", "bid", "ask", "price", "volume"])

        divider = self._get_point_divider(symbol)
        fmt = ">IIIff"
        records = []

        base_ts = base_hour.replace(minute=0, second=0, microsecond=0)

        for i in range(num_ticks):
            offset = i * record_size
            ms_offset, ask_raw, bid_raw, ask_vol, bid_vol = struct.unpack_from(fmt, decomp, offset)

            # Time calculation
            tick_dt = base_ts + timedelta(milliseconds=ms_offset)
            bid = bid_raw / divider
            ask = ask_raw / divider
            price = (bid + ask) / 2.0
            volume = float(ask_vol + bid_vol)

            records.append((tick_dt, bid, ask, price, volume))

        df = pd.DataFrame(records, columns=["timestamp", "bid", "ask", "price", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df

    async def fetch_hour_ticks(
        self,
        symbol: str,
        dt_utc: datetime,
        client: Optional[httpx.AsyncClient] = None,
        force_redownload: bool = False,
    ) -> pd.DataFrame:
        """Fetch and parse ticks for a single UTC hour, using disk cache if available."""
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        else:
            dt_utc = dt_utc.astimezone(timezone.utc)

        raw_path = self._get_raw_file_path(symbol, dt_utc)

        # Check local cache
        if not force_redownload and raw_path.exists() and raw_path.stat().st_size > 0:
            with open(raw_path, "rb") as f:
                data = f.read()
            return self.parse_bi5_ticks(data, symbol, dt_utc)

        url = self._build_hour_url(symbol, dt_utc)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
            "Accept": "*/*",
        }

        should_close_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=15.0)
            should_close_client = True

        raw_bytes = b""
        try:
            for attempt in range(1, self.max_retries + 1):
                try:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        raw_bytes = resp.content
                        # Cache raw bytes immutably
                        with open(raw_path, "wb") as f:
                            f.write(raw_bytes)
                        break
                    elif resp.status_code == 404:
                        # Market closed (weekend, holiday) or zero ticks
                        with open(raw_path, "wb") as f:
                            f.write(b"")
                        break
                    elif resp.status_code in (429, 503, 502):
                        wait_sec = (self.backoff_factor ** attempt)
                        logger.warning(f"Dukascopy {resp.status_code} at {url}, retrying in {wait_sec:.1f}s...")
                        await asyncio.sleep(wait_sec)
                    else:
                        resp.raise_for_status()
                except (httpx.TransportError, httpx.TimeoutException) as err:
                    if attempt == self.max_retries:
                        logger.warning(f"Failed to fetch {url} after {self.max_retries} attempts: {err}")
                    else:
                        await asyncio.sleep(self.backoff_factor ** attempt)
        finally:
            if should_close_client:
                await client.aclose()

        return self.parse_bi5_ticks(raw_bytes, symbol, dt_utc)

    def ticks_to_candles(
        self,
        ticks_df: pd.DataFrame,
        symbol: str = "XAUUSD",
        timeframe: str = "1m",
    ) -> pd.DataFrame:
        """Aggregate tick DataFrame into canonical OHLCV candle DataFrame."""
        if ticks_df.empty:
            return pd.DataFrame(columns=[
                "timestamp", "open", "high", "low", "close", "volume", "source", "instrument"
            ])

        rule = self.TIMEFRAME_RESAMPLE_MAP.get(timeframe, "1min")
        df = ticks_df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

        df = df.set_index("timestamp").sort_index()

        ohlc = df["price"].resample(rule, label="left", closed="left").ohlc()
        vol = df["volume"].resample(rule, label="left", closed="left").sum()

        resampled = pd.concat([ohlc, vol], axis=1).dropna().reset_index()
        resampled.rename(columns={"volume": "volume"}, inplace=True)

        resampled["source"] = "DUKASCOPY"
        resampled["instrument"] = symbol.upper()

        return validate_candle_dataframe(resampled)

    def _get_checkpoint_path(self, symbol: str, timeframe: str) -> Path:
        return self.checkpoints_dir / f"checkpoint_{symbol.upper()}_{timeframe}.json"

    def _load_checkpoint(self, symbol: str, timeframe: str) -> Optional[datetime]:
        ckpt_path = self._get_checkpoint_path(symbol, timeframe)
        if ckpt_path.exists():
            try:
                with open(ckpt_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return datetime.fromisoformat(data["last_completed_hour"]).replace(tzinfo=timezone.utc)
            except Exception:
                return None
        return None

    def _save_checkpoint(self, symbol: str, timeframe: str, dt_utc: datetime) -> None:
        ckpt_path = self._get_checkpoint_path(symbol, timeframe)
        with open(ckpt_path, "w", encoding="utf-8") as f:
            json.dump({
                "symbol": symbol.upper(),
                "timeframe": timeframe,
                "last_completed_hour": dt_utc.isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)

    async def fetch_candles(
        self,
        symbol: str = "XAUUSD",
        timeframe: str = "1m",
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        use_checkpoint: bool = True,
        on_progress: Optional[Callable[[datetime, int, int], None]] = None,
    ) -> pd.DataFrame:
        """Download tick data over date range and aggregate to canonical candles."""
        if start_time is None:
            # Default to last 24 hours
            start_time = datetime.now(timezone.utc) - timedelta(days=1)
        if end_time is None:
            end_time = datetime.now(timezone.utc)

        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        # Truncate to hour starts
        start_hour = start_time.replace(minute=0, second=0, microsecond=0)
        end_hour = end_time.replace(minute=0, second=0, microsecond=0)

        if use_checkpoint:
            last_done = self._load_checkpoint(symbol, timeframe)
            if last_done and last_done >= start_hour and last_done < end_hour:
                logger.info(f"Resuming Dukascopy download from checkpoint: {last_done}")
                start_hour = last_done + timedelta(hours=1)

        # Generate hours sequence
        hours: List[datetime] = []
        curr = start_hour
        while curr <= end_hour:
            hours.append(curr)
            curr += timedelta(hours=1)

        if not hours:
            logger.info("No new hours to download for Dukascopy.")
            return pd.DataFrame(columns=[
                "timestamp", "open", "high", "low", "close", "volume", "source", "instrument"
            ])

        total_hours = len(hours)
        logger.info(f"Downloading {total_hours} hours of Dukascopy {symbol} tick data from {hours[0]} to {hours[-1]}")

        all_ticks: List[pd.DataFrame] = []

        async with httpx.AsyncClient(timeout=20.0) as client:
            for idx, hour_dt in enumerate(hours):
                ticks_df = await self.fetch_hour_ticks(symbol, hour_dt, client=client)
                if not ticks_df.empty:
                    all_ticks.append(ticks_df)

                if use_checkpoint:
                    self._save_checkpoint(symbol, timeframe, hour_dt)

                if on_progress:
                    on_progress(hour_dt, idx + 1, total_hours)

                if self.request_delay > 0:
                    await asyncio.sleep(self.request_delay)

        if not all_ticks:
            logger.warning(f"No ticks retrieved from Dukascopy for {symbol} between {start_time} and {end_time}")
            return pd.DataFrame(columns=[
                "timestamp", "open", "high", "low", "close", "volume", "source", "instrument"
            ])

        combined_ticks = pd.concat(all_ticks, ignore_index=True)
        combined_ticks = combined_ticks.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)

        candles_df = self.ticks_to_candles(combined_ticks, symbol=symbol, timeframe=timeframe)

        # Filter strictly within requested bounds
        candles_df = candles_df[
            (candles_df["timestamp"] >= start_time) & (candles_df["timestamp"] <= end_time)
        ].reset_index(drop=True)

        logger.info(f"Generated {len(candles_df)} {timeframe} candles from {len(combined_ticks)} Dukascopy ticks.")
        return candles_df

    async def ingest_and_store(
        self,
        symbol: str = "XAUUSD",
        timeframe: str = "1m",
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        storage_manager: Optional[ParquetStorageManager] = None,
        quality_engine: Optional[DataQualityEngine] = None,
    ) -> Tuple[pd.DataFrame, QualityReport, Path]:
        """End-to-end ingestion pipeline: fetch, quality audit, and Parquet storage."""
        sm = storage_manager or ParquetStorageManager()
        qe = quality_engine or DataQualityEngine()

        raw_df = await self.fetch_candles(
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_time,
            end_time=end_time,
        )

        clean_df, quality_report = qe.validate_and_clean(
            raw_df,
            instrument=symbol,
            source="DUKASCOPY",
        )

        parquet_path = sm.save_candles(
            clean_df,
            instrument=symbol,
            timeframe=timeframe,
            source="DUKASCOPY",
            quality_score=quality_report.quality_score,
            is_resampled=False,
        )

        return clean_df, quality_report, parquet_path

