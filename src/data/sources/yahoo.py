"""Yahoo Finance public historical market data connector for Gold."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Optional
import httpx
import pandas as pd
from src.core.config import settings
from src.core.logger import logger
from src.data.models import validate_candle_dataframe


class YahooFinanceClient:
    """Free public historical Gold data downloader using Yahoo Finance Chart API."""

    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"

    TIMEFRAME_MAP = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1h",
        "1d": "1d",
    }

    def __init__(self, raw_storage_dir: Optional[Path] = None):
        self.raw_dir = raw_storage_dir or (settings.RAW_DATA_DIR / "yahoo")
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    async def fetch_candles(
        self,
        symbol: str = "GC=F",
        timeframe: str = "1h",
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        range_str: Optional[str] = "1mo",
    ) -> pd.DataFrame:
        """Download candles from Yahoo Finance."""
        interval = self.TIMEFRAME_MAP.get(timeframe, "1h")
        url = f"{self.BASE_URL}/{symbol}"

        params = {"interval": interval}
        if start_time and end_time:
            params["period1"] = int(start_time.timestamp())
            params["period2"] = int(end_time.timestamp())
        elif range_str:
            params["range"] = range_str
        else:
            params["range"] = "1mo"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        }

        logger.info(f"Downloading historical candles from Yahoo Finance: {symbol} [{interval}] params={params}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                logger.error(f"Failed to fetch data from Yahoo Finance: {e}")
                raise

        # Save raw payload
        ts_now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_sym = symbol.replace("=", "_")
        raw_file = self.raw_dir / f"raw_{safe_sym}_{interval}_{ts_now}.json"
        with open(raw_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
        logger.info(f"Saved immutable raw Yahoo Finance data to: {raw_file}")

        # Parse OHLCV
        chart = data.get("chart", {})
        result_list = chart.get("result", [])
        if not result_list:
            error = chart.get("error", "No result found in chart response")
            raise ValueError(f"Yahoo Finance returned no data: {error}")

        result = result_list[0]
        timestamps = result.get("timestamp", [])
        quote = result.get("indicators", {}).get("quote", [{}])[0]

        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])
        volumes = quote.get("volume", [])

        records = []
        for i, ts in enumerate(timestamps):
            o = opens[i] if i < len(opens) else None
            h = highs[i] if i < len(highs) else None
            l = lows[i] if i < len(lows) else None
            c = closes[i] if i < len(closes) else None
            v = volumes[i] if i < len(volumes) else 0

            if None in (o, h, l, c):
                continue

            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            records.append({
                "timestamp": dt,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v) if v is not None else 0.0,
                "source": "YAHOO",
                "instrument": "GC=F" if symbol == "GC=F" else symbol,
            })

        df = pd.DataFrame(records)
        if df.empty:
            raise ValueError(f"No valid OHLCV candles parsed from Yahoo Finance response for {symbol}")

        return validate_candle_dataframe(df)
