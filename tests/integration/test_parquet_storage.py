"""Integration tests for Parquet storage manager."""

import pandas as pd
import pytest
from src.data.storage.parquet_manager import ParquetStorageManager


def test_parquet_atomic_save_and_load(tmp_path):
    mgr = ParquetStorageManager(base_dir=tmp_path)
    dates = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "timestamp": dates,
        "open": [2000.0] * 10,
        "high": [2010.0] * 10,
        "low": [1990.0] * 10,
        "close": [2005.0] * 10,
        "volume": [100.0] * 10,
        "source": "TEST",
        "instrument": "XAUUSD",
    })

    saved_file = mgr.save_candles(df, "XAUUSD", "1h", "TEST", quality_score=98.5)
    assert saved_file.exists()

    loaded_df = mgr.load_candles("XAUUSD", "1h")
    assert len(loaded_df) == 10
    assert loaded_df["close"].iloc[0] == 2005.0

    # Verify registry
    datasets = mgr.list_datasets()
    assert len(datasets) == 1
    assert datasets[0]["instrument"] == "XAUUSD"
    assert datasets[0]["quality_score"] == 98.5
