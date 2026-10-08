"""Deterministic trend and breakout indicators: ADX and Donchian Channels."""

from typing import Tuple
import numpy as np
import pandas as pd
from src.quant.indicators.volatility import atr


def donchian_channels(df: pd.DataFrame, period: int = 20) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Donchian Channels: (upper, lower, midline)."""
    upper = df["high"].rolling(window=period, min_periods=period).max()
    lower = df["low"].rolling(window=period, min_periods=period).min()
    midline = (upper + lower) / 2.0
    return upper, lower, midline


def adx(df: pd.DataFrame, period: int = 14) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Average Directional Index: (adx, plus_di, minus_di)."""
    high = df["high"]
    low = df["low"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr_val = atr(df, period=period)

    plus_dm_series = pd.Series(plus_dm, index=df.index).ewm(alpha=1.0 / period, adjust=False).mean()
    minus_dm_series = pd.Series(minus_dm, index=df.index).ewm(alpha=1.0 / period, adjust=False).mean()

    plus_di = 100.0 * (plus_dm_series / tr_val.replace(0, np.nan))
    minus_di = 100.0 * (minus_dm_series / tr_val.replace(0, np.nan))

    dx = 100.0 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx_series = dx.ewm(alpha=1.0 / period, adjust=False).mean().fillna(0.0)

    return adx_series, plus_di.fillna(0.0), minus_di.fillna(0.0)
