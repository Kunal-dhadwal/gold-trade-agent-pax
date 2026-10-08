"""Unit and regression tests for the Data Quality Engine."""

from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest
from src.data.validation.quality_engine import DataQualityEngine


def test_clean_data_passes_with_perfect_score(tmp_path):
    """Test that valid clean data passes with a perfect 100 score."""
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
    assert report.nan_inf_bars == 0
    pd.testing.assert_series_equal(clean_df["close"], df["close"])


def test_corrupted_ohlc_and_duplicates_detected_and_handled(tmp_path):
    """Test detection and handling of geometry violations, duplicates, and negative volumes."""
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


def test_nan_and_none_ohlcv_quarantined_and_detected(tmp_path):
    """Regression test: Ensure NaN and None values in any OHLCV column are quarantined."""
    engine = DataQualityEngine(reports_dir=tmp_path)
    dates = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "timestamp": dates,
        "open": [2000.0] * 10,
        "high": [2005.0] * 10,
        "low": [1995.0] * 10,
        "close": [2002.0] * 10,
        "volume": [100.0] * 10,
        "source": "TEST",
        "instrument": "XAUUSD",
    })

    # Inject NaNs and Nones across distinct bars
    df.loc[0, "open"] = np.nan
    df.loc[2, "high"] = None
    df.loc[4, "low"] = np.nan
    df.loc[6, "close"] = np.nan
    df.loc[8, "volume"] = np.nan

    clean_df, report = engine.validate_and_clean(df, "XAUUSD", "TEST")

    # 5 bars must be flagged as nan_inf_bars
    assert report.nan_inf_bars == 5
    # Only the 5 clean bars (indices 1, 3, 5, 7, 9) must survive
    assert len(clean_df) == 5
    # Absolutely zero NaNs may exist in clean_df
    assert clean_df["open"].isna().sum() == 0
    assert clean_df["high"].isna().sum() == 0
    assert clean_df["low"].isna().sum() == 0
    assert clean_df["close"].isna().sum() == 0
    assert clean_df["volume"].isna().sum() == 0
    assert report.quality_score < 100.0


def test_infinite_values_quarantined_and_detected(tmp_path):
    """Regression test: Ensure +inf and -inf in OHLCV are detected and quarantined."""
    engine = DataQualityEngine(reports_dir=tmp_path)
    dates = pd.date_range("2024-01-01", periods=6, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "timestamp": dates,
        "open": [2000.0] * 6,
        "high": [2005.0] * 6,
        "low": [1995.0] * 6,
        "close": [2002.0] * 6,
        "volume": [100.0] * 6,
        "source": "TEST",
        "instrument": "XAUUSD",
    })

    # Inject +inf and -inf
    df.loc[1, "high"] = float("inf")
    df.loc[3, "low"] = float("-inf")
    df.loc[5, "volume"] = float("inf")

    clean_df, report = engine.validate_and_clean(df, "XAUUSD", "TEST")

    assert report.nan_inf_bars == 3
    assert len(clean_df) == 3
    # Surviving rows are indices 0, 2, 4
    assert np.isfinite(clean_df["high"]).all()
    assert np.isfinite(clean_df["low"]).all()
    assert np.isfinite(clean_df["volume"]).all()


def test_non_numeric_and_empty_dataframe(tmp_path):
    """Test handling of non-numeric garbage and empty dataframes."""
    engine = DataQualityEngine(reports_dir=tmp_path)

    # Empty df
    empty_df = pd.DataFrame()
    clean_empty, rep_empty = engine.validate_and_clean(empty_df, "XAUUSD", "TEST")
    assert clean_empty.empty
    assert rep_empty.quality_score == 0.0

    # Non-numeric garbage in close
    dates = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "timestamp": dates,
        "open": [2000.0, 2001.0, 2002.0],
        "high": [2005.0, 2006.0, 2007.0],
        "low": [1995.0, 1996.0, 1997.0],
        "close": [2002.0, "CORRUPTED_TEXT", 2004.0],
        "volume": [100.0, 100.0, 100.0],
    })

    clean_df, report = engine.validate_and_clean(df, "XAUUSD", "TEST")
    assert report.nan_inf_bars == 1
    assert len(clean_df) == 2
    assert (clean_df["close"] > 2000.0).all()
