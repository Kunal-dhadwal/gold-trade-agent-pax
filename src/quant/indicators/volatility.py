"""Deterministic volatility indicators: ATR and Bollinger Bands."""

from typing import Tuple
import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    """Calculate True Range for OHLC DataFrame."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range using Wilder's smoothing."""
    tr = true_range(df)
    # Wilder's exponential smoothing
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def bollinger_bands(
    series: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    """Calculate Bollinger Bands: (upper, middle, lower, bandwidth, percent_b)."""
    middle = series.rolling(window=period, min_periods=period).mean()
    std = series.rolling(window=period, min_periods=period).std()

    upper = middle + (num_std * std)
    lower = middle - (num_std * std)
    bandwidth = (upper - lower) / middle.replace(0, np.nan)
    percent_b = (series - lower) / (upper - lower).replace(0, np.nan)

    return upper, middle, lower, bandwidth, percent_b
