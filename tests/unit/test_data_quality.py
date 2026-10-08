"""Unit tests for the Data Quality Engine."""

from datetime import datetime, timezone
import pandas as pd
import pytest
from src.data.validation.quality_engine import DataQualityEngine


def test_clean_data_passes_with_perfect_score(tmp_path):
    engine = DataQualityEngine(reports_dir=tmp_path)
    dates = pd.date_range("2024-01-01", periods=50, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "timestamp": dates,
        "open": [2000.0 + i for i in range(50)],
        "high": [2005.0 + i for i in range(50)],
        "low": [1995.0 + i for i in range(50)],
        "close": [2002.0 + i for i in range(50)],
        "volume": [100.0 for _ in range(50)],
        "source": "TEST",
        "instrument": "XAUUSD",
    })

    clean_df, report = engine.validate_and_clean(df, "XAUUSD", "TEST")
    assert len(clean_df) == 50
    assert report.quality_score == 100.0
    assert report.duplicate_timestamps == 0
    assert report.invalid_ohlc_bars == 0


def test_corrupted_ohlc_and_duplicates_detected_and_handled(tmp_path):
    engine = DataQualityEngine(reports_dir=tmp_path)
    dates = list(pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC"))
    # Add duplicate timestamp
    dates.append(dates[2])

    df = pd.DataFrame({
        "timestamp": dates,
        "open": [2000.0] * 11,
        "high": [2005.0] * 11,
        "low": [1995.0] * 11,
        "close": [2002.0] * 11,
        "volume": [100.0] * 11,
        "source": "TEST",
        "instrument": "XAUUSD",
    })

    # Corrupt bar 0: High < Low
    df.loc[0, "high"] = 1990.0
    df.loc[0, "low"] = 2010.0

    # Corrupt bar 1: Low > Close
    df.loc[1, "low"] = 2005.0

    # Corrupt bar 4: Negative volume
    df.loc[4, "volume"] = -50.0

    clean_df, report = engine.validate_and_clean(df, "XAUUSD", "TEST")

    assert report.duplicate_timestamps == 1
    assert report.invalid_ohlc_bars == 2
    assert report.quality_score < 100.0
    assert (clean_df["high"] >= clean_df["low"]).all()
    assert (clean_df["volume"] >= 0).all()
