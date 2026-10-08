"""Unit tests for multi-timeframe resampling."""

import pandas as pd
import pytest
from src.data.resampler import TimeframeResampler


def test_timeframe_resampling_aggregation():
    # 60 1-minute bars spanning 1 hour
    dates = pd.date_range("2024-01-01 00:00:00", periods=60, freq="1min", tz="UTC")
    df = pd.DataFrame({
        "timestamp": dates,
        "open": [2000.0 + i for i in range(60)],
        "high": [2005.0 + i for i in range(60)],
        "low": [1995.0 + i for i in range(60)],
        "close": [2001.0 + i for i in range(60)],
        "volume": [10.0 for _ in range(60)],
        "source": "TEST",
        "instrument": "XAUUSD",
    })

    # Resample to 1h
    h1_df = TimeframeResampler.resample(df, "1h")
    assert len(h1_df) == 1
    assert h1_df["open"].iloc[0] == 2000.0  # First open
    assert h1_df["close"].iloc[0] == 2001.0 + 59  # Last close
    assert h1_df["high"].iloc[0] == 2005.0 + 59  # Max high
    assert h1_df["low"].iloc[0] == 1995.0  # Min low
    assert h1_df["volume"].iloc[0] == 600.0  # Sum of volume


def test_timeframe_resampling_edge_cases():
    # Empty df returns empty
    empty_df = pd.DataFrame()
    assert TimeframeResampler.resample(empty_df, "1h").empty

    # Unsupported timeframe raises ValueError
    dates = pd.date_range("2024-01-01 00:00:00", periods=5, freq="1min", tz="UTC")
    df = pd.DataFrame({
        "timestamp": dates,
        "open": [2000.0] * 5,
        "high": [2005.0] * 5,
        "low": [1995.0] * 5,
        "close": [2001.0] * 5,
        "volume": [10.0] * 5,
        "source": "TEST",
        "instrument": "XAUUSD",
    })
    with pytest.raises(ValueError, match="Unsupported target timeframe"):
        TimeframeResampler.resample(df, "2h")

    # Generate all timeframes
    res_map = TimeframeResampler.generate_all_timeframes(df)
    assert "5m" in res_map
    assert "15m" in res_map
    assert "1h" in res_map
    assert len(res_map["5m"]) == 1

