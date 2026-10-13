"""Dataset Import and Quality Certification CLI for AGentTrXPAUS.

Allows importing, auditing, quality-certifying, and persisting user-provided
CSV, Parquet, or JSON historical market data files into canonical storage.

Governing specifications:
- Phase 1 Technical Specification (Section 3)
- Milestone 2.5.4 Post-Acceptance Remediation (Phase B)
- ADR-008: Anti-Overfitting Architecture & OOS Quarantine
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from src.cli.strategy_creator import compute_file_sha256, parse_yahoo_raw_json
from src.core.config import settings
from src.core.logger import logger
from src.data.models import validate_candle_dataframe
from src.data.sources.local import LocalFileClient
from src.data.storage.parquet_manager import ParquetStorageManager
from src.data.validation.quality_engine import DataQualityEngine, QualityReport
from src.strategy.contracts import InstrumentEnum, TimeframeEnum


def parse_instrument(val: str) -> str:
    """Normalize and validate target trading instrument."""
    clean = val.strip().upper()
    if clean in ("XAUUSD", "GOLD", "XAU"):
        return "XAUUSD"
    if clean in ("GC=F", "GC_F", "GC"):
        return "GC=F"
    if clean in ("PAXGUSDT", "PAXG"):
        return "PAXGUSDT"
    return clean


def parse_json_market_data(file_path: Path, instrument: str = "XAUUSD") -> pd.DataFrame:
    """Parse JSON market data files (Yahoo Finance, Delta Exchange, or records list)."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        if "chart" in data:
            return parse_yahoo_raw_json(file_path, instrument=instrument)
        elif "result" in data and isinstance(data["result"], list):
            df = pd.DataFrame(data["result"])
            if "time" in df.columns:
                df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
            elif "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df["source"] = "DELTA_RAW_JSON"
            df["instrument"] = instrument
            return validate_candle_dataframe(df)
        elif "data" in data and isinstance(data["data"], list):
            df = pd.DataFrame(data["data"])
            df["source"] = "JSON_ARCHIVE"
            df["instrument"] = instrument
            return validate_candle_dataframe(df)
    elif isinstance(data, list):
        df = pd.DataFrame(data)
        df["source"] = "JSON_ARCHIVE"
        df["instrument"] = instrument
        return validate_candle_dataframe(df)

    raise ValueError(f"Unrecognized JSON market data format in {file_path}")


def import_and_certify_dataset(
    file_path: Path,
    instrument: str,
    timeframe: str = "1h",
    source: str = "LOCAL_IMPORT",
    expected_frequency_seconds: int = 3600,
    storage_dir: Optional[Path] = None,
) -> Tuple[Path, QualityReport, Dict[str, Any]]:
    """Import, audit via DataQualityEngine, and persist dataset to canonical storage."""
    if not file_path.exists():
        raise FileNotFoundError(f"Input market data file not found: {file_path}")

    input_sha256 = compute_file_sha256(file_path)
    file_size_bytes = file_path.stat().st_size
    suffix = file_path.suffix.lower()

    logger.info(f"Loading external dataset from {file_path} ({file_size_bytes} bytes, SHA256: {input_sha256[:8]}...)")

    # 1. Load raw data
    if suffix in (".parquet", ".pq"):
        df = LocalFileClient.load_file(file_path, instrument=instrument, source=source)
    elif suffix in (".csv", ".txt"):
        df = LocalFileClient.load_file(file_path, instrument=instrument, source=source)
    elif suffix == ".json":
        df = parse_json_market_data(file_path, instrument=instrument)
    else:
        raise ValueError(f"Unsupported market data file format: {suffix}")

    total_input_rows = len(df)
    if total_input_rows == 0:
        raise ValueError(f"Input dataset is empty: {file_path}")

    # 2. Run DataQualityEngine Audit
    quality_engine = DataQualityEngine()
    clean_df, quality_report = quality_engine.validate_and_clean(
        df=df,
        instrument=instrument,
        source=source,
        expected_frequency_seconds=expected_frequency_seconds,
        save_report=True,
    )

    if clean_df.empty:
        raise ValueError(
            f"Zero valid bars remained after DataQualityEngine filtering. Score: {quality_report.quality_score}"
        )

    # 3. Persist to Canonical Parquet Storage
    storage_manager = ParquetStorageManager(base_dir=storage_dir)
    canonical_parquet_path = storage_manager.save_candles(
        df=clean_df,
        instrument=instrument,
        timeframe=timeframe,
        source=source,
        quality_score=quality_report.quality_score,
        is_resampled=False,
    )

    canonical_sha256 = compute_file_sha256(canonical_parquet_path)

    # 4. Construct Provenance Manifest
    manifest = {
        "instrument": instrument,
        "timeframe": timeframe,
        "source": source,
        "input_file_path": str(file_path.resolve()),
        "input_sha256": input_sha256,
        "input_size_bytes": file_size_bytes,
        "total_input_rows": total_input_rows,
        "valid_bars_retained": len(clean_df),
        "rejected_rows_count": total_input_rows - len(clean_df),
        "quality_score": round(quality_report.quality_score, 2),
        "start_time": clean_df["timestamp"].iloc[0].isoformat(),
        "end_time": clean_df["timestamp"].iloc[-1].isoformat(),
        "canonical_parquet_path": str(canonical_parquet_path.resolve()),
        "canonical_sha256": canonical_sha256,
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }

    return canonical_parquet_path, quality_report, manifest


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI parser for dataset importer."""
    parser = argparse.ArgumentParser(
        prog="python -m src.cli.import_dataset",
        description="AGentTrXPAUS — Historical Market Data Importer & Quality Certifier",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--file",
        type=str,
        required=True,
        help="Path to external dataset file (.parquet, .csv, .json)",
    )
    parser.add_argument(
        "--instrument",
        type=str,
        default="XAUUSD",
        help="Target trading instrument (XAUUSD, GC=F, PAXGUSDT)",
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        default="1h",
        help="Bar timeframe interval (1m, 5m, 15m, 30m, 1h, 4h, 1d)",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="LOCAL_ARCHIVE",
        help="Provenance source tag (e.g. DUKASCOPY, YAHOO, INSTITUTIONAL_BROKER)",
    )
    parser.add_argument(
        "--expected-frequency",
        type=int,
        default=3600,
        help="Expected candle interval in seconds (default: 3600 for 1h)",
    )
    parser.add_argument(
        "--manifest-out",
        type=str,
        default=None,
        help="Optional path to export JSON dataset provenance manifest",
    )
    return parser


def main() -> int:
    """CLI entrypoint."""
    parser = build_arg_parser()
    args = parser.parse_args()

    input_path = Path(args.file)
    instrument = parse_instrument(args.instrument)

    print("=" * 80)
    print("AGentTrXPAUS — Historical Dataset Importer & Quality Certifier")
    print("=" * 80)
    print(f"Input File:        {input_path}")
    print(f"Instrument:        {instrument}")
    print(f"Timeframe:         {args.timeframe}")
    print(f"Source Tag:        {args.source}")
    print("=" * 80)

    try:
        parquet_path, report, manifest = import_and_certify_dataset(
            file_path=input_path,
            instrument=instrument,
            timeframe=args.timeframe,
            source=args.source,
            expected_frequency_seconds=args.expected_frequency,
        )
    except Exception as e:
        logger.error(f"Dataset import failed: {e}", exc_info=True)
        print(f"\n[ERROR] Import failed: {e}", file=sys.stderr)
        return 1

    print("\n" + "=" * 80)
    print("DATASET QUALITY CERTIFICATION")
    print("=" * 80)
    print(f"Quality Score:     {report.quality_score:.2f} / 100.0")
    print(f"Input Bars:        {manifest['total_input_rows']}")
    print(f"Valid Bars:        {manifest['valid_bars_retained']}")
    print(f"Rejected Bars:     {manifest['rejected_rows_count']}")
    print(f"Chronological Span: {manifest['start_time']} -> {manifest['end_time']}")
    print(f"Canonical Parquet: {parquet_path}")
    print(f"SHA-256 Checksum:  {manifest['canonical_sha256']}")
    print("=" * 80)

    # Save manifest if requested
    if args.manifest_out:
        out_manifest_path = Path(args.manifest_out)
        out_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        print(f"Saved Provenance Manifest: {out_manifest_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
