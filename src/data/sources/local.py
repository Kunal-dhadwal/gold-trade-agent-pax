"""Local dataset ingestion loader for CSV and Parquet files."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union
import pandas as pd
from src.core.logger import logger
from src.data.models import validate_candle_dataframe


class LocalFileClient:
    """Loads and standardizes local CSV or Parquet market data files."""

    @staticmethod
    def load_file(
        file_path: Union[str, Path],
        instrument: str = "XAUUSD",
        source: str = "LOCAL_ARCHIVE",
    ) -> pd.DataFrame:
        """Load and normalize local CSV or Parquet file."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        logger.info(f"Loading local dataset from: {path}")

        if path.suffix.lower() in (".parquet", ".pq"):
            df = pd.read_parquet(path)
        elif path.suffix.lower() in (".csv", ".txt"):
            df = pd.read_csv(path)
        else:
            raise ValueError(f"Unsupported file format: {path.suffix}")

        # Column standardization mapping
        col_map = {}
        for col in df.columns:
            c_lower = col.strip().lower()
            if c_lower in ("timestamp", "time", "date", "datetime"):
                col_map[col] = "timestamp"
            elif c_lower in ("open", "o"):
                col_map[col] = "open"
            elif c_lower in ("high", "h"):
                col_map[col] = "high"
            elif c_lower in ("low", "l"):
                col_map[col] = "low"
            elif c_lower in ("close", "c"):
                col_map[col] = "close"
            elif c_lower in ("volume", "vol", "v"):
                col_map[col] = "volume"

        df = df.rename(columns=col_map)
        if "volume" not in df.columns:
            df["volume"] = 0.0
        df["source"] = source
        df["instrument"] = instrument

        return validate_candle_dataframe(df)
