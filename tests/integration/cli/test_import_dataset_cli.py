"""Integration tests for the Dataset Importer CLI (src/cli/import_dataset.py).

Verifies command-line parsing, instrument normalization, quality auditing,
canonical parquet export, SHA-256 hashing, and provenance manifest generation.
"""

import json
from pathlib import Path
import pytest
import pandas as pd

from src.cli.import_dataset import (
    build_arg_parser,
    import_and_certify_dataset,
    parse_instrument,
)


def test_parse_instrument_normalization():
    """Verify instrument normalization rules."""
    assert parse_instrument("xauusd") == "XAUUSD"
    assert parse_instrument("gold") == "XAUUSD"
    assert parse_instrument("XAU") == "XAUUSD"
    assert parse_instrument("gc=f") == "GC=F"
    assert parse_instrument("GC_F") == "GC=F"
    assert parse_instrument("paxgusdt") == "PAXGUSDT"
    assert parse_instrument("BTCUSDT") == "BTCUSDT"


def test_import_dataset_arg_parser():
    """Verify arg parser defaults and custom options."""
    parser = build_arg_parser()
    args = parser.parse_args(["--file", "data/dummy.csv"])

    assert args.file == "data/dummy.csv"
    assert args.instrument == "XAUUSD"
    assert args.timeframe == "1h"
    assert args.source == "LOCAL_ARCHIVE"
    assert args.expected_frequency == 3600
    assert args.manifest_out is None

    custom_args = parser.parse_args(
        [
            "--file", "data/custom.parquet",
            "--instrument", "GC=F",
            "--timeframe", "15m",
            "--source", "DUKASCOPY",
            "--expected-frequency", "900",
            "--manifest-out", "data/manifest.json",
        ]
    )
    assert custom_args.instrument == "GC=F"
    assert custom_args.timeframe == "15m"
    assert custom_args.source == "DUKASCOPY"
    assert custom_args.expected_frequency == 900
    assert custom_args.manifest_out == "data/manifest.json"


def test_import_and_certify_real_yahoo_json():
    """Verify import of raw Yahoo JSON dataset with full DataQualityEngine certification."""
    raw_path = Path("data/raw/yahoo/raw_GC_F_1h_20261008_053213.json")
    assert raw_path.exists(), "Raw Yahoo GC=F file must exist."

    parquet_path, report, manifest = import_and_certify_dataset(
        file_path=raw_path,
        instrument="GC=F",
        timeframe="1h",
        source="YAHOO_RAW_JSON",
        expected_frequency_seconds=3600,
    )

    assert parquet_path.exists()
    assert report.quality_score >= 90.0
    assert report.valid_candles == 510

    assert manifest["instrument"] == "GC=F"
    assert manifest["timeframe"] == "1h"
    assert manifest["total_input_rows"] == 510
    assert manifest["valid_bars_retained"] == 510
    assert manifest["rejected_rows_count"] == 0
    assert len(manifest["canonical_sha256"]) == 64
    assert len(manifest["input_sha256"]) == 64


def test_import_and_certify_custom_csv(tmp_path):
    """Verify import of custom user-provided CSV dataset."""
    csv_file = tmp_path / "custom_xau_1h.csv"
    
    # Create valid synthetic CSV data
    timestamps = pd.date_range("2024-01-01 00:00:00", periods=50, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [2000.0 + i * 0.5 for i in range(50)],
            "high": [2005.0 + i * 0.5 for i in range(50)],
            "low": [1995.0 + i * 0.5 for i in range(50)],
            "close": [2002.0 + i * 0.5 for i in range(50)],
            "volume": [1000.0 for _ in range(50)],
        }
    )
    df.to_csv(csv_file, index=False)

    parquet_path, report, manifest = import_and_certify_dataset(
        file_path=csv_file,
        instrument="XAUUSD",
        timeframe="1h",
        source="USER_CUSTOM_CSV",
        expected_frequency_seconds=3600,
        storage_dir=tmp_path,
    )

    assert parquet_path.exists()
    assert report.quality_score >= 90.0
    assert report.valid_candles == 50
    assert manifest["total_input_rows"] == 50
    assert manifest["valid_bars_retained"] == 50
    assert manifest["source"] == "USER_CUSTOM_CSV"


def test_import_and_certify_delta_raw_json(tmp_path):
    """Verify import of raw Delta Exchange JSON payload."""
    raw_delta_path = Path("data/raw/delta/raw_delta_PAXGUSDT_1h_20261008_053350.json")
    assert raw_delta_path.exists(), "Raw Delta PAXGUSDT file must exist."

    parquet_path, report, manifest = import_and_certify_dataset(
        file_path=raw_delta_path,
        instrument="PAXGUSDT",
        timeframe="1h",
        source="DELTA",
        expected_frequency_seconds=3600,
        storage_dir=tmp_path,
    )

    assert parquet_path.exists()
    assert report.valid_candles == 164
    assert manifest["instrument"] == "PAXGUSDT"
    assert manifest["total_input_rows"] == 164
    assert manifest["valid_bars_retained"] == 164


def test_import_and_certify_dukascopy_csv(tmp_path):
    """Verify import of Dukascopy-style CSV with Gmt time header."""
    csv_file = tmp_path / "dukascopy_xauusd_sample.csv"
    timestamps = [f"{day:02d}.01.2024 {hour:02d}:00:00.000" for day in range(1, 4) for hour in range(24)]
    df = pd.DataFrame({
        "Gmt time": timestamps[:60],
        "Open": [2060.0 + i * 0.1 for i in range(60)],
        "High": [2065.0 + i * 0.1 for i in range(60)],
        "Low": [2058.0 + i * 0.1 for i in range(60)],
        "Close": [2062.0 + i * 0.1 for i in range(60)],
        "Volume": [50.0 for _ in range(60)],
    })
    df.to_csv(csv_file, index=False)

    parquet_path, report, manifest = import_and_certify_dataset(
        file_path=csv_file,
        instrument="XAUUSD",
        timeframe="1h",
        source="DUKASCOPY_CSV",
        expected_frequency_seconds=3600,
        storage_dir=tmp_path,
    )

    assert parquet_path.exists()
    assert report.valid_candles == 60
    assert manifest["instrument"] == "XAUUSD"
    assert manifest["total_input_rows"] == 60


def test_import_nonexistent_file_raises():
    """Verify non-existent file error handling."""
    with pytest.raises(FileNotFoundError):
        import_and_certify_dataset(
            file_path=Path("non_existent_file.csv"),
            instrument="XAUUSD",
        )

