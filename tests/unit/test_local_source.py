"""Unit tests for LocalFileClient CSV and Parquet loaders."""

from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import pytest
from src.data.sources.local import LocalFileClient


def test_local_file_client_csv(tmp_path):
    csv_file = tmp_path / "test_data.csv"
    df_raw = pd.DataFrame({
        "time": ["2024-01-01 00:00:00", "2024-01-01 01:00:00"],
        "o": [2000.0, 2010.0],
        "h": [2020.0, 2030.0],
        "l": [1990.0, 2005.0],
        "c": [2010.0, 2025.0],
        "vol": [50.0, 60.0],
    })
    df_raw.to_csv(csv_file, index=False)

    df_loaded = LocalFileClient.load_file(csv_file, instrument="XAUUSD", source="LOCAL_CSV")
    assert len(df_loaded) == 2
    assert "timestamp" in df_loaded.columns
    assert df_loaded["open"].iloc[0] == 2000.0
    assert df_loaded["instrument"].iloc[0] == "XAUUSD"
    assert df_loaded["source"].iloc[0] == "LOCAL_CSV"


def test_local_file_client_parquet(tmp_path):
    pq_file = tmp_path / "test_data.parquet"
    df_raw = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=2, freq="1h", tz="UTC"),
        "open": [2000.0, 2010.0],
        "high": [2020.0, 2030.0],
        "low": [1990.0, 2005.0],
        "close": [2010.0, 2025.0],
        "volume": [50.0, 60.0],
    })
    df_raw.to_parquet(pq_file, index=False)

    df_loaded = LocalFileClient.load_file(pq_file, instrument="GC=F", source="LOCAL_PQ")
    assert len(df_loaded) == 2
    assert df_loaded["instrument"].iloc[0] == "GC=F"


def test_local_file_client_errors(tmp_path):
    # Non-existent file
    with pytest.raises(FileNotFoundError):
        LocalFileClient.load_file(tmp_path / "does_not_exist.csv")

    # Unsupported extension
    unsupported = tmp_path / "data.json"
    unsupported.write_text("{}")
    with pytest.raises(ValueError, match="Unsupported file format"):
        LocalFileClient.load_file(unsupported)

