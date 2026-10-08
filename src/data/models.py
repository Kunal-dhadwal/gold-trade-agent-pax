"""Candle schemas and DataFrame contract definitions."""

from datetime import datetime, timezone
from typing import List, Optional
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator


class Candle(BaseModel):
    timestamp: datetime
    open: float = Field(gt=0, description="Opening price")
    high: float = Field(gt=0, description="Highest price")
    low: float = Field(gt=0, description="Lowest price")
    close: float = Field(gt=0, description="Closing price")
    volume: float = Field(ge=0, description="Volume traded")
    source: str = "UNKNOWN"
    instrument: str = "XAUUSD"

    @field_validator("timestamp")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @field_validator("high")
    @classmethod
    def validate_high(cls, v: float, values) -> float:
        return v


def validate_candle_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure DataFrame has required columns, types, and timezone."""
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    clean_df = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(clean_df["timestamp"]):
        clean_df["timestamp"] = pd.to_datetime(clean_df["timestamp"], utc=True)
    elif clean_df["timestamp"].dt.tz is None:
        clean_df["timestamp"] = clean_df["timestamp"].dt.tz_localize("UTC")
    else:
        clean_df["timestamp"] = clean_df["timestamp"].dt.tz_convert("UTC")

    numeric_cols = ["open", "high", "low", "close", "volume"]
    for col in numeric_cols:
        clean_df[col] = pd.to_numeric(clean_df[col], errors="coerce")

    if "source" not in clean_df.columns:
        clean_df["source"] = "UNKNOWN"
    if "instrument" not in clean_df.columns:
        clean_df["instrument"] = "XAUUSD"

    return clean_df.sort_values("timestamp").reset_index(drop=True)
