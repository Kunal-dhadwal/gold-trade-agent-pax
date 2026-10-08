"""Gold market trading session identification."""

from datetime import datetime
import pandas as pd
from src.core.constants import MarketSession


def get_market_session(dt: datetime) -> MarketSession:
    """Identify Gold market session for a given UTC datetime."""
    hour = dt.hour
    if 13 <= hour < 16:
        return MarketSession.LONDON_NY_OVERLAP
    elif 8 <= hour < 16:
        return MarketSession.LONDON
    elif 13 <= hour < 21:
        return MarketSession.NEW_YORK
    elif 0 <= hour < 8:
        return MarketSession.ASIAN
    else:
        return MarketSession.OFF_HOURS


def tag_sessions(df: pd.DataFrame) -> pd.Series:
    """Add MarketSession category column to candle DataFrame."""
    if df.empty or "timestamp" not in df.columns:
        return pd.Series(dtype="str")
    return df["timestamp"].map(lambda ts: get_market_session(ts).value)
