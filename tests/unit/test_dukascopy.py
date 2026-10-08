"""Unit tests for Dukascopy XAUUSD historical market data connector."""

from datetime import datetime, timedelta, timezone
import lzma
from pathlib import Path
import struct
import tempfile
import numpy as np
import pandas as pd
import pytest
from src.data.sources.dukascopy import DukascopyClient
from src.data.storage.parquet_manager import ParquetStorageManager
from src.data.validation.quality_engine import DataQualityEngine


def create_synthetic_bi5_bytes(
    base_price: float = 2050.0,
    point_divider: float = 1000.0,
    num_ticks: int = 120,
) -> bytes:
    """Create a valid LZMA-compressed bi5 payload for testing."""
    records = []
    # Generate 120 ticks spread across an hour (every 30 seconds = 30,000 ms)
    for i in range(num_ticks):
        ms_offset = i * 30000  # up to 3,600,000 ms (1 hr)
        price_offset = np.sin(i / 10.0) * 2.0
        ask = int((base_price + price_offset + 0.15) * point_divider)
        bid = int((base_price + price_offset - 0.15) * point_divider)
        ask_vol = 1.5 + (i % 3)
        bid_vol = 1.0 + (i % 2)
        records.append(struct.pack(">IIIff", ms_offset, ask, bid, ask_vol, bid_vol))

    raw_uncompressed = b"".join(records)
    return lzma.compress(raw_uncompressed)


@pytest.fixture
def temp_dukascopy_env(tmp_path):
    raw_dir = tmp_path / "raw" / "dukascopy"
    storage_dir = tmp_path / "storage"
    reports_dir = tmp_path / "reports"

    client = DukascopyClient(raw_storage_dir=raw_dir)
    storage_mgr = ParquetStorageManager(base_dir=storage_dir)
    quality_engine = DataQualityEngine(reports_dir=reports_dir)

    return client, storage_mgr, quality_engine, raw_dir, storage_dir, reports_dir


def test_dukascopy_parse_bi5_ticks():
    """Verify decompression and unpacking of 20-byte bi5 tick records."""
    client = DukascopyClient()
    base_hour = datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
    raw_bytes = create_synthetic_bi5_bytes(base_price=2050.0, num_ticks=60)

    df = client.parse_bi5_ticks(raw_bytes, symbol="XAUUSD", base_hour=base_hour)

    assert len(df) == 60
    assert list(df.columns) == ["timestamp", "bid", "ask", "price", "volume"]
    assert df["timestamp"].iloc[0] == base_hour
    assert df["timestamp"].iloc[-1] == base_hour + timedelta(milliseconds=59 * 30000)

    # Check price divider (1000 for XAUUSD)
    assert 2045.0 < df["bid"].iloc[0] < 2055.0
    assert 2045.0 < df["ask"].iloc[0] < 2055.0
    assert df["ask"].iloc[0] > df["bid"].iloc[0]
    assert df["volume"].iloc[0] > 0


def test_dukascopy_empty_and_corrupt_payload():
    """Test handling of empty or corrupt bi5 payloads."""
    client = DukascopyClient()
    base_hour = datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)

    # Empty payload
    empty_df = client.parse_bi5_ticks(b"", symbol="XAUUSD", base_hour=base_hour)
    assert empty_df.empty

    # Corrupt payload
    corrupt_df = client.parse_bi5_ticks(b"not_lzma_data", symbol="XAUUSD", base_hour=base_hour)
    assert corrupt_df.empty


def test_dukascopy_ticks_to_candles_aggregation():
    """Verify ticks are deterministically converted to canonical OHLCV bars."""
    client = DukascopyClient()
    base_hour = datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
    raw_bytes = create_synthetic_bi5_bytes(base_price=2050.0, num_ticks=120)
    ticks_df = client.parse_bi5_ticks(raw_bytes, symbol="XAUUSD", base_hour=base_hour)

    candles_1m = client.ticks_to_candles(ticks_df, symbol="XAUUSD", timeframe="1m")

    assert not candles_1m.empty
    assert (candles_1m["source"] == "DUKASCOPY").all()
    assert (candles_1m["instrument"] == "XAUUSD").all()

    # Geometry assertion
    assert (candles_1m["high"] >= candles_1m["low"]).all()
    assert (candles_1m["high"] >= candles_1m["open"]).all()
    assert (candles_1m["high"] >= candles_1m["close"]).all()
    assert (candles_1m["low"] <= candles_1m["open"]).all()
    assert (candles_1m["low"] <= candles_1m["close"]).all()
    assert (candles_1m["volume"] > 0).all()


@pytest.mark.asyncio
async def test_dukascopy_local_caching_and_deterministic_reprocessing(temp_dukascopy_env):
    """Test that downloaded raw files are cached on disk and deterministic when reprocessed."""
    client, storage_mgr, quality_engine, raw_dir, storage_dir, reports_dir = temp_dukascopy_env

    test_hour = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    raw_path = client._get_raw_file_path("XAUUSD", test_hour)

    # Write synthetic raw file to cache
    synthetic_payload = create_synthetic_bi5_bytes(base_price=2040.0, num_ticks=100)
    with open(raw_path, "wb") as f:
        f.write(synthetic_payload)

    # First fetch from cache
    ticks_1 = await client.fetch_hour_ticks("XAUUSD", test_hour)
    candles_1 = client.ticks_to_candles(ticks_1, symbol="XAUUSD", timeframe="1m")

    # Second fetch from cache
    ticks_2 = await client.fetch_hour_ticks("XAUUSD", test_hour)
    candles_2 = client.ticks_to_candles(ticks_2, symbol="XAUUSD", timeframe="1m")

    # Exact deterministic equivalence
    pd.testing.assert_frame_equal(candles_1, candles_2)
    assert len(candles_1) > 0


@pytest.mark.asyncio
async def test_dukascopy_ingest_and_store_end_to_end(temp_dukascopy_env):
    """Test complete pipeline: fetch, quality validation, Parquet storage, and registry update."""
    client, storage_mgr, quality_engine, raw_dir, storage_dir, reports_dir = temp_dukascopy_env

    # Seed 2 hours of synthetic data
    for h in [12, 13]:
        hour_dt = datetime(2024, 1, 15, h, 0, 0, tzinfo=timezone.utc)
        path = client._get_raw_file_path("XAUUSD", hour_dt)
        with open(path, "wb") as f:
            f.write(create_synthetic_bi5_bytes(base_price=2060.0 + h, num_ticks=60))

    start_time = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    end_time = datetime(2024, 1, 15, 13, 59, 59, tzinfo=timezone.utc)

    clean_df, report, parquet_path = await client.ingest_and_store(
        symbol="XAUUSD",
        timeframe="5m",
        start_time=start_time,
        end_time=end_time,
        storage_manager=storage_mgr,
        quality_engine=quality_engine,
    )

    assert not clean_df.empty
    assert report.quality_score == 100.0
    assert parquet_path.exists()
    assert (clean_df["source"] == "DUKASCOPY").all()
    assert (clean_df["instrument"] == "XAUUSD").all()

    # Load back from Parquet and verify
    loaded_df = storage_mgr.load_candles(instrument="XAUUSD", timeframe="5m")
    assert len(loaded_df) == len(clean_df)
    assert loaded_df["timestamp"].iloc[0] == clean_df["timestamp"].iloc[0]

