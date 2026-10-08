"""Delta Exchange public market data REST connector."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
import pandas as pd
from src.core.config import settings
from src.core.logger import logger
from src.data.models import validate_candle_dataframe


class DeltaExchangeClient:
    """Public market data client for Delta Exchange."""

    TIMEFRAME_MAP = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1h",
        "4h": "4h",
        "1d": "1d",
    }

    def __init__(self, base_url: Optional[str] = None, raw_storage_dir: Optional[Path] = None):
        self.base_url = (base_url or settings.DELTA_API_BASE_URL).rstrip("/")
        self.raw_dir = raw_storage_dir or (settings.RAW_DATA_DIR / "delta")
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    async def get_products(self) -> List[Dict[str, Any]]:
        """Fetch list of tradeable products from Delta Exchange."""
        url = f"{self.base_url}/v2/products"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", [])

    async def get_gold_products(self) -> List[Dict[str, Any]]:
        """Filter products to find Gold / XAUUSD derivatives."""
        products = await self.get_products()
        gold_prods = [
            p for p in products
            if "XAU" in p.get("symbol", "").upper() or "GOLD" in p.get("symbol", "").upper()
        ]
        return gold_prods

    async def fetch_candles(
        self,
        symbol: str = "XAUUSD",
        timeframe: str = "1h",
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 500,
    ) -> pd.DataFrame:
        """Fetch historical candles from Delta Exchange /v2/history/candles."""
        resolution = self.TIMEFRAME_MAP.get(timeframe, "1h")
        url = f"{self.base_url}/v2/history/candles"

        params: Dict[str, Any] = {
            "symbol": symbol,
            "resolution": resolution,
        }

        if start_time:
            params["start"] = int(start_time.timestamp())
        if end_time:
            params["end"] = int(end_time.timestamp())

        logger.info(f"Fetching Delta Exchange candles: {symbol} [{resolution}] params={params}")

        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                logger.error(f"Delta Exchange candle request failed: {e}")
                raise

        # Save immutable raw payload
        ts_now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        raw_file = self.raw_dir / f"raw_delta_{symbol}_{resolution}_{ts_now}.json"
        with open(raw_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
        logger.info(f"Saved immutable raw Delta Exchange payload to {raw_file}")

        candles_raw = data.get("result", [])
        if not candles_raw:
            # Fallback check if wrapped differently
            candles_raw = data if isinstance(data, list) else []

        records = []
        for c in candles_raw:
            # Delta returns candles as dict or list: [time, open, high, low, close, volume]
            if isinstance(c, dict):
                t = c.get("time") or c.get("timestamp")
                o = c.get("open")
                h = c.get("high")
                l = c.get("low")
                cl = c.get("close")
                v = c.get("volume", 0)
            elif isinstance(c, (list, tuple)) and len(c) >= 6:
                t, o, h, l, cl, v = c[0], c[1], c[2], c[3], c[4], c[5]
            else:
                continue

            if None in (t, o, h, l, cl):
                continue

            # Delta timestamp is in seconds or microseconds
            ts_sec = t if t < 1e11 else t / 1e6
            dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)

            records.append({
                "timestamp": dt,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(cl),
                "volume": float(v) if v is not None else 0.0,
                "source": "DELTA_EXCHANGE",
                "instrument": symbol,
            })

        df = pd.DataFrame(records)
        if df.empty:
            logger.warning(f"No candles returned from Delta Exchange for {symbol}")
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "source", "instrument"])

        return validate_candle_dataframe(df)
