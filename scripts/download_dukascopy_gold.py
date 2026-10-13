#!/usr/bin/env python3
"""Standalone Host-Side Historical Gold Data Downloader (Dukascopy).

Designed to be executed on the host machine OUTSIDE the sandbox environment
where network access is permitted.

Features:
- Memory-Safe: Aggregates ticks to candles hour-by-hour (never accumulates 100M+ ticks in RAM).
- Resumable: Caches raw .bi5 files to disk and skips already-downloaded hours.
- Direct Output: Outputs clean, UTC-normalized CSV ready for src.cli.import_dataset.

Usage:
    python scripts/download_dukascopy_gold.py \
        --symbol XAUUSD \
        --start 2021-01-01 \
        --end 2024-12-31 \
        --timeframe 1h \
        --out data/raw/external/XAUUSD_1h_2021_2024.csv
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import lzma
from pathlib import Path
import struct
import sys
import time
from typing import List, Optional, Tuple
import urllib.request
import urllib.error

import pandas as pd


DUKASCOPY_BASE_URL = "http://datafeed.dukascopy.com/datafeed"
POINT_DIVIDER_MAP = {
    "XAUUSD": 1000.0,
    "EURUSD": 100000.0,
    "GBPUSD": 100000.0,
    "USDJPY": 1000.0,
}


def build_hour_url(symbol: str, dt_utc: datetime) -> str:
    """Build Dukascopy bi5 tick URL. Dukascopy uses 0-indexed months."""
    year = dt_utc.year
    month_idx = dt_utc.month - 1
    day = dt_utc.day
    hour = dt_utc.hour
    return f"{DUKASCOPY_BASE_URL}/{symbol.upper()}/{year}/{month_idx:02d}/{day:02d}/{hour:02d}h_ticks.bi5"


def parse_bi5_ticks(
    raw_bytes: bytes,
    symbol: str,
    base_hour: datetime,
    raw_path_to_clean: Optional[Path] = None,
) -> List[Tuple[datetime, float, float]]:
    """Unpack LZMA bi5 bytes into list of (timestamp, price, volume) tuples."""
    if not raw_bytes:
        return []

    try:
        decomp = lzma.decompress(raw_bytes)
    except Exception:
        # If cache file exists and is corrupt, remove it to allow redownload
        if raw_path_to_clean and raw_path_to_clean.exists() and raw_path_to_clean.stat().st_size > 0:
            try:
                raw_path_to_clean.unlink()
            except OSError:
                pass
        return []

    record_size = 20
    num_ticks = len(decomp) // record_size
    if num_ticks == 0:
        return []

    divider = POINT_DIVIDER_MAP.get(symbol.upper(), 1000.0)
    fmt = ">IIIff"
    records = []
    base_ts = base_hour.replace(minute=0, second=0, microsecond=0)

    for i in range(num_ticks):
        offset = i * record_size
        ms_offset, ask_raw, bid_raw, ask_vol, bid_vol = struct.unpack_from(fmt, decomp, offset)
        tick_dt = base_ts + timedelta(milliseconds=ms_offset)
        bid = bid_raw / divider
        ask = ask_raw / divider
        price = (bid + ask) / 2.0
        volume = float(ask_vol + bid_vol)
        records.append((tick_dt, price, volume))

    return records


def ticks_to_candle_records(
    ticks: List[Tuple[datetime, float, float]],
    timeframe: str = "1h",
) -> List[dict]:
    """Aggregate hourly ticks into candle dictionary."""
    if not ticks:
        return []

    df = pd.DataFrame(ticks, columns=["timestamp", "price", "volume"])
    rule = "1h" if timeframe == "1h" else ("1min" if timeframe == "1m" else "5min")
    df = df.set_index("timestamp").sort_index()

    ohlc = df["price"].resample(rule, label="left", closed="left").ohlc()
    vol = df["volume"].resample(rule, label="left", closed="left").sum()
    resampled = pd.concat([ohlc, vol], axis=1).dropna().reset_index()

    records = []
    for _, row in resampled.iterrows():
        records.append({
            "timestamp": row["timestamp"].isoformat(),
            "open": round(float(row["open"]), 3),
            "high": round(float(row["high"]), 3),
            "low": round(float(row["low"]), 3),
            "close": round(float(row["close"]), 3),
            "volume": round(float(row["volume"]), 2),
        })
    return records


def get_hour_cache_path(symbol: str, dt_utc: datetime, cache_dir: Path) -> Path:
    """Get canonical path for local raw bi5 cache."""
    year = dt_utc.year
    month_idx = dt_utc.month - 1
    day = dt_utc.day
    hour = dt_utc.hour
    hour_cache_dir = cache_dir / symbol.upper() / str(year) / f"{month_idx:02d}" / f"{day:02d}"
    hour_cache_dir.mkdir(parents=True, exist_ok=True)
    return hour_cache_dir / f"{hour:02d}h_ticks.bi5"


def download_hour(
    symbol: str,
    dt_utc: datetime,
    cache_dir: Path,
    max_retries: int = 3,
) -> Tuple[bytes, Path]:
    """Download single hour bi5 payload with local caching."""
    raw_path = get_hour_cache_path(symbol, dt_utc, cache_dir)

    if raw_path.exists():
        with open(raw_path, "rb") as f:
            return f.read(), raw_path

    url = build_hour_url(symbol, dt_utc)
    headers = {"User-Agent": "AGentTrXPAUS-Downloader/1.0"}
    req = urllib.request.Request(url, headers=headers)

    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                data = resp.read()
                with open(raw_path, "wb") as f:
                    f.write(data)
                return data, raw_path
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # Market closed (weekend, holiday)
                with open(raw_path, "wb") as f:
                    f.write(b"")
                return b"", raw_path
            elif e.code in (429, 503):
                time.sleep(1.0 * attempt)
            else:
                break
        except Exception:
            time.sleep(0.5 * attempt)

    return b"", raw_path


def get_resume_start_time(out_path: Path, start_dt: datetime) -> Tuple[datetime, int]:
    """Inspect existing output CSV and determine resume timestamp and bar count."""
    if not out_path.exists() or out_path.stat().st_size == 0:
        return start_dt, 0

    try:
        df = pd.read_csv(out_path)
        if df.empty or "timestamp" not in df.columns:
            return start_dt, 0

        last_ts_str = df["timestamp"].iloc[-1]
        last_dt = datetime.fromisoformat(last_ts_str)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        else:
            last_dt = last_dt.astimezone(timezone.utc)

        # Resume from next hour
        resume_dt = last_dt + timedelta(hours=1)
        if resume_dt > start_dt:
            return resume_dt, len(df)
        return start_dt, len(df)
    except Exception:
        return start_dt, 0


def main():
    parser = argparse.ArgumentParser(description="Standalone Dukascopy Historical Data Downloader")
    parser.add_argument("--symbol", default="XAUUSD", help="Symbol to download (default: XAUUSD)")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--timeframe", default="1h", help="Timeframe (1h, 1m, 5m)")
    parser.add_argument("--out", default="data/raw/external/XAUUSD_1h_downloaded.csv", help="Output CSV path")
    parser.add_argument("--cache-dir", default="data/raw/dukascopy", help="Local bi5 raw cache directory")
    parser.add_argument("--force-restart", action="store_true", help="Overwrite output CSV and do not resume")
    args = parser.parse_args()

    start_dt = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc, hour=0, minute=0, second=0)
    end_dt = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc, hour=23, minute=0, second=0)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Handle resume vs fresh start
    if args.force_restart and out_path.exists():
        out_path.unlink()

    curr_start, existing_candles = get_resume_start_time(out_path, start_dt)
    is_resuming = curr_start > start_dt and out_path.exists()

    print("=" * 80)
    print("AGentTrXPAUS — Host-Side Historical Gold Downloader")
    print("=" * 80)
    print(f"Symbol:          {args.symbol}")
    print(f"Requested Span:  {start_dt} -> {end_dt}")
    if is_resuming:
        print(f"Resuming From:   {curr_start} (Existing bars: {existing_candles})")
    else:
        print(f"Mode:            Fresh download")
    print(f"Timeframe:       {args.timeframe}")
    print(f"Output File:     {out_path}")
    print(f"Cache Dir:       {cache_dir}")
    print("=" * 80)

    # Open CSV for incremental writing
    csv_mode = "a" if is_resuming else "w"
    csv_file = open(out_path, csv_mode, encoding="utf-8")
    if not is_resuming:
        csv_file.write("timestamp,open,high,low,close,volume\n")
        csv_file.flush()

    curr = curr_start
    total_hours = int((end_dt - start_dt).total_seconds() // 3600) + 1
    hours_done = int((curr_start - start_dt).total_seconds() // 3600)
    total_candles = existing_candles
    start_wall = time.time()

    try:
        while curr <= end_dt:
            raw_bytes, raw_path = download_hour(args.symbol, curr, cache_dir)
            if raw_bytes:
                ticks = parse_bi5_ticks(raw_bytes, args.symbol, curr, raw_path_to_clean=raw_path)
                if ticks:
                    candles = ticks_to_candle_records(ticks, timeframe=args.timeframe)
                    for c in candles:
                        csv_file.write(f"{c['timestamp']},{c['open']},{c['high']},{c['low']},{c['close']},{c['volume']}\n")
                    csv_file.flush()
                    total_candles += len(candles)

            hours_done += 1
            if hours_done % 100 == 0 or curr == end_dt:
                elapsed = time.time() - start_wall
                pct = (hours_done / total_hours) * 100
                hrs_per_sec = (hours_done - int((curr_start - start_dt).total_seconds() // 3600)) / max(elapsed, 0.001)
                rem_sec = (total_hours - hours_done) / max(hrs_per_sec, 0.001) if hrs_per_sec > 0 else 0
                print(
                    f"Progress: {hours_done}/{total_hours} hrs ({pct:.1f}%) | "
                    f"Saved: {total_candles} candles | Speed: {hrs_per_sec:.1f} hrs/s | ETA: {rem_sec / 60:.1f}m",
                    end="\r",
                    flush=True,
                )

            curr += timedelta(hours=1)
    finally:
        csv_file.close()

    print("\n\n" + "=" * 80)
    print("DOWNLOAD AND INCREMENTAL AGGREGATION COMPLETE")
    print("=" * 80)
    print(f"Total Hours Checked:  {hours_done}")
    print(f"Total Candles Saved:  {total_candles}")
    print(f"Output File:          {out_path.resolve()}")
    print("=" * 80)
    print("\nNext Step: Run the verified importer CLI:")
    print(f"  python -m src.cli.import_dataset --file {out_path} --instrument {args.symbol} --timeframe {args.timeframe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


