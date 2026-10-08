"""Parquet storage engine with PyArrow, atomic persistence, and metadata tracking."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from src.core.config import settings
from src.core.logger import logger
from src.data.models import validate_candle_dataframe


class ParquetStorageManager:
    """Manages normalized and resampled market datasets in Apache Parquet format."""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or settings.DATA_DIR
        self.normalized_dir = self.base_dir / "normalized"
        self.timeframe_dir = self.base_dir / "timeframe"
        self.metadata_dir = self.base_dir / "metadata"

        self.normalized_dir.mkdir(parents=True, exist_ok=True)
        self.timeframe_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_dir.mkdir(parents=True, exist_ok=True)

        self.registry_file = self.metadata_dir / "dataset_registry.json"
        self._ensure_registry()

    def _ensure_registry(self) -> None:
        if not self.registry_file.exists():
            with open(self.registry_file, "w", encoding="utf-8") as f:
                json.dump({"datasets": []}, f, indent=2)

    def _compute_sha256(self, file_path: Path) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                sha256.update(chunk)
        return sha256.hexdigest()

    def save_candles(
        self,
        df: pd.DataFrame,
        instrument: str,
        timeframe: str,
        source: str = "UNKNOWN",
        quality_score: float = 100.0,
        is_resampled: bool = False,
    ) -> Path:
        """Atomically persist validated candles to Parquet and update dataset registry."""
        if df.empty:
            raise ValueError(f"Cannot save empty DataFrame for {instrument} [{timeframe}]")

        clean_df = validate_candle_dataframe(df)

        target_dir = self.timeframe_dir if is_resampled else self.normalized_dir
        sym_clean = instrument.replace("=", "_").replace("/", "_")
        dest_dir = target_dir / sym_clean
        dest_dir.mkdir(parents=True, exist_ok=True)

        target_file = dest_dir / f"{timeframe}.parquet"
        temp_file = dest_dir / f"{timeframe}.parquet.tmp"

        # Convert to PyArrow Table
        table = pa.Table.from_pandas(clean_df, preserve_index=False)
        pq.write_table(table, temp_file, compression="SNAPPY")

        # Atomic rename
        temp_file.replace(target_file)
        checksum = self._compute_sha256(target_file)
        file_size = target_file.stat().st_size

        logger.info(f"Persisted {len(clean_df)} bars to {target_file} (SHA256: {checksum[:8]}...)")

        # Update metadata registry
        meta_entry = {
            "instrument": instrument,
            "source": source,
            "timeframe": timeframe,
            "is_resampled": is_resampled,
            "candle_count": len(clean_df),
            "start_time": clean_df["timestamp"].iloc[0].isoformat(),
            "end_time": clean_df["timestamp"].iloc[-1].isoformat(),
            "file_path": str(target_file),
            "file_size_bytes": file_size,
            "checksum_sha256": checksum,
            "quality_score": round(quality_score, 2),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._update_registry(meta_entry)

        return target_file

    def _update_registry(self, entry: Dict[str, Any]) -> None:
        try:
            with open(self.registry_file, "r", encoding="utf-8") as f:
                reg = json.load(f)
        except Exception:
            reg = {"datasets": []}

        datasets = reg.get("datasets", [])
        # Update existing or append
        updated = False
        for i, d in enumerate(datasets):
            if d.get("instrument") == entry["instrument"] and d.get("timeframe") == entry["timeframe"] and d.get("is_resampled") == entry["is_resampled"]:
                datasets[i] = entry
                updated = True
                break

        if not updated:
            datasets.append(entry)

        reg["datasets"] = datasets
        with open(self.registry_file, "w", encoding="utf-8") as f:
            json.dump(reg, f, indent=2)

    def load_candles(
        self,
        instrument: str,
        timeframe: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """Load candles from Parquet storage with optional time filtering."""
        sym_clean = instrument.replace("=", "_").replace("/", "_")

        # Look in normalized or timeframe folders
        cand_files = [
            self.normalized_dir / sym_clean / f"{timeframe}.parquet",
            self.timeframe_dir / sym_clean / f"{timeframe}.parquet",
        ]

        target_file = None
        for f in cand_files:
            if f.exists():
                target_file = f
                break

        if not target_file:
            raise FileNotFoundError(f"No stored Parquet dataset for {instrument} [{timeframe}] at {[str(x) for x in cand_files]}")

        df = pd.read_parquet(target_file)
        df = validate_candle_dataframe(df)

        if start_time:
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
            df = df[df["timestamp"] >= start_time]

        if end_time:
            if end_time.tzinfo is None:
                end_time = end_time.replace(tzinfo=timezone.utc)
            df = df[df["timestamp"] <= end_time]

        return df.reset_index(drop=True)

    def list_datasets(self) -> List[Dict[str, Any]]:
        """List all datasets in registry."""
        with open(self.registry_file, "r", encoding="utf-8") as f:
            reg = json.load(f)
        return reg.get("datasets", [])
