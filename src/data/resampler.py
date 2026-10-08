"""Deterministic multi-timeframe resampling engine for OHLCV candles."""

from typing import Dict
import pandas as pd
from src.core.logger import logger
from src.data.models import validate_candle_dataframe


PANDAS_FREQ_MAP = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1D",
}


class TimeframeResampler:
    """Resamples base OHLCV candles to higher timeframes adhering to standard UTC bars."""

    @staticmethod
    def resample(df: pd.DataFrame, target_timeframe: str) -> pd.DataFrame:
        """Resample DataFrame to target timeframe (e.g. '5m', '15m', '1h', '4h', '1d')."""
        if df.empty:
            return df

        clean_df = validate_candle_dataframe(df)
        freq = PANDAS_FREQ_MAP.get(target_timeframe)
        if not freq:
            raise ValueError(f"Unsupported target timeframe: {target_timeframe}. Supported: {list(PANDAS_FREQ_MAP.keys())}")

        # Set timestamp as datetime index for resampling
        indexed_df = clean_df.set_index("timestamp")

        agg_dict = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }

        # Resample with closed='left', label='left' for standard candle open timestamp
        resampled = indexed_df.resample(freq, closed="left", label="left").agg(agg_dict)

        # Drop empty buckets where no trades occurred
        resampled = resampled.dropna(subset=["open", "close"])

        resampled["source"] = clean_df["source"].iloc[0] if "source" in clean_df.columns else "RESAMPLED"
        resampled["instrument"] = clean_df["instrument"].iloc[0] if "instrument" in clean_df.columns else "XAUUSD"

        result = resampled.reset_index()
        logger.info(f"Resampled {len(clean_df)} base bars into {len(result)} [{target_timeframe}] bars.")
        return validate_candle_dataframe(result)

    @classmethod
    def generate_all_timeframes(cls, df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """Generate full hierarchy of standard timeframes from 1m base candles."""
        tf_results = {}
        for tf in ["5m", "15m", "30m", "1h", "4h", "1d"]:
            try:
                tf_results[tf] = cls.resample(df, tf)
            except Exception as e:
                logger.error(f"Failed to resample to {tf}: {e}")
        return tf_results
