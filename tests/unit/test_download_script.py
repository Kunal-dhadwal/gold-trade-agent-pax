"""Offline Unit & Verification Tests for scripts/download_dukascopy_gold.py.

Verifies .bi5 unpacking, URL construction, 0-indexed month encoding,
candle aggregation, weekend closure caching, corrupt payload handling,
and incremental resumption.
"""

from datetime import datetime, timedelta, timezone
import lzma
from pathlib import Path
import struct
import tempfile
import pandas as pd
import pytest

from scripts.download_dukascopy_gold import (
    build_hour_url,
    download_hour,
    parse_bi5_ticks,
    ticks_to_candle_records,
)


def create_mock_bi5_payload(base_price: float = 2050.0, num_ticks: int = 60) -> bytes:
    """Create synthetic LZMA-compressed bi5 payload for testing."""
    records = []
    divider = 1000.0
    for i in range(num_ticks):
        ms_offset = i * 60000  # 1 tick per minute = 60,000 ms
        ask = int((base_price + 0.20 + (i * 0.05)) * divider)
        bid = int((base_price - 0.20 + (i * 0.05)) * divider)
        ask_vol = 1.0
        bid_vol = 1.5
        records.append(struct.pack(">IIIff", ms_offset, ask, bid, ask_vol, bid_vol))

    return lzma.compress(b"".join(records))


def test_build_hour_url_zero_indexed_months():
    """Verify Dukascopy URL formatting and 0-indexed month conversion."""
    # January (Month 1 -> 00)
    jan_dt = datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
    url_jan = build_hour_url("XAUUSD", jan_dt)
    assert url_jan == "http://datafeed.dukascopy.com/datafeed/XAUUSD/2024/00/15/14h_ticks.bi5"

    # December (Month 12 -> 11)
    dec_dt = datetime(2023, 12, 31, 23, 0, 0, tzinfo=timezone.utc)
    url_dec = build_hour_url("XAUUSD", dec_dt)
    assert url_dec == "http://datafeed.dukascopy.com/datafeed/XAUUSD/2023/11/31/23h_ticks.bi5"


def test_parse_bi5_ticks_format_and_point_divider():
    """Verify >IIIff binary record parsing and 1000.0 point divider for XAUUSD."""
    base_hour = datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
    raw_payload = create_mock_bi5_payload(base_price=2050.0, num_ticks=60)

    ticks = parse_bi5_ticks(raw_payload, symbol="XAUUSD", base_hour=base_hour)

    assert len(ticks) == 60
    # First tick: ms_offset=0, ask=2050.2, bid=2049.8 -> mid=2050.0
    t0, p0, v0 = ticks[0]
    assert t0 == base_hour
    assert abs(p0 - 2050.0) < 0.001
    assert abs(v0 - 2.5) < 0.001  # ask_vol(1.0) + bid_vol(1.5)

    # Last tick: ms_offset=59*60000 -> 59 minutes past the hour
    t_last, p_last, v_last = ticks[-1]
    assert t_last == base_hour + timedelta(minutes=59)
    assert p_last > p0


def test_parse_bi5_ticks_empty_and_corrupt():
    """Verify corrupt and empty payloads do not crash parser."""
    base_hour = datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)

    # Empty
    assert parse_bi5_ticks(b"", "XAUUSD", base_hour) == []

    # Corrupted
    assert parse_bi5_ticks(b"corrupt_lzma_bytes", "XAUUSD", base_hour) == []


def test_ticks_to_candle_records_aggregation():
    """Verify ticks aggregate to canonical left-closed OHLCV candles."""
    base_hour = datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
    raw_payload = create_mock_bi5_payload(base_price=2050.0, num_ticks=60)
    ticks = parse_bi5_ticks(raw_payload, symbol="XAUUSD", base_hour=base_hour)

    candles = ticks_to_candle_records(ticks, timeframe="1h")
    assert len(candles) == 1

    c = candles[0]
    assert c["timestamp"] == base_hour.isoformat()
    assert c["open"] <= c["high"]
    assert c["low"] <= c["close"]
    assert c["low"] <= c["open"]
    assert c["high"] >= c["close"]
    assert c["volume"] == round(60 * 2.5, 2)


def test_download_hour_local_cache(tmp_path):
    """Verify local cache lookup prevents re-downloading existing hours."""
    cache_dir = tmp_path / "dukascopy_cache"
    dt = datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)

    # Pre-populate cache with mock payload
    mock_payload = create_mock_bi5_payload()
    hour_dir = cache_dir / "XAUUSD" / "2024" / "00" / "15"
    hour_dir.mkdir(parents=True, exist_ok=True)
    raw_file = hour_dir / "14h_ticks.bi5"
    raw_file.write_bytes(mock_payload)

    # download_hour should read directly from disk cache without network
    cached_data, found_path = download_hour("XAUUSD", dt, cache_dir)
    assert cached_data == mock_payload
    assert found_path == raw_file


def test_corrupt_cache_purge_on_parse_failure(tmp_path):
    """Verify corrupt bi5 file on disk is unlinked when decompress fails."""
    base_hour = datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
    corrupt_file = tmp_path / "corrupt_14h_ticks.bi5"
    corrupt_file.write_bytes(b"not_lzma_garbage_data")

    assert corrupt_file.exists()
    ticks = parse_bi5_ticks(
        raw_bytes=b"not_lzma_garbage_data",
        symbol="XAUUSD",
        base_hour=base_hour,
        raw_path_to_clean=corrupt_file,
    )
    assert ticks == []
    # Verify file was cleaned up so it can be re-downloaded
    assert not corrupt_file.exists()


def test_get_resume_start_time(tmp_path):
    """Verify resume logic correctly finds next hour to process from existing CSV."""
    from scripts.download_dukascopy_gold import get_resume_start_time

    out_csv = tmp_path / "existing_candles.csv"
    start_dt = datetime(2021, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    # 1. Non-existent file starts at start_dt
    resume_dt, count = get_resume_start_time(out_csv, start_dt)
    assert resume_dt == start_dt
    assert count == 0

    # 2. Existing file with 2 bars resumes from bar 2 + 1 hour
    out_csv.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2021-01-01T00:00:00+00:00,1890.1,1892.5,1888.0,1891.2,100.0\n"
        "2021-01-01T01:00:00+00:00,1891.2,1893.0,1890.5,1892.0,120.0\n"
    )
    resume_dt, count = get_resume_start_time(out_csv, start_dt)
    assert resume_dt == datetime(2021, 1, 1, 2, 0, 0, tzinfo=timezone.utc)
    assert count == 2

