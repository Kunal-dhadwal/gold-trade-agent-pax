"""Automated data quality validation and auditing engine."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from src.core.config import settings
from src.core.logger import logger


@dataclass
class QualityReport:
    instrument: str
    source: str
    total_input_candles: int
    valid_candles: int
    quality_score: float  # 0 to 100
    duplicate_timestamps: int
    out_of_order_timestamps: int
    invalid_ohlc_bars: int
    zero_negative_volumes: int
    abnormal_price_spikes: int
    gaps_detected: int
    date_range_start: Optional[str] = None
    date_range_end: Optional[str] = None
    issues_summary: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instrument": self.instrument,
            "source": self.source,
            "total_input_candles": self.total_input_candles,
            "valid_candles": self.valid_candles,
            "quality_score": round(self.quality_score, 2),
            "duplicate_timestamps": self.duplicate_timestamps,
            "out_of_order_timestamps": self.out_of_order_timestamps,
            "invalid_ohlc_bars": self.invalid_ohlc_bars,
            "zero_negative_volumes": self.zero_negative_volumes,
            "abnormal_price_spikes": self.abnormal_price_spikes,
            "gaps_detected": self.gaps_detected,
            "date_range_start": self.date_range_start,
            "date_range_end": self.date_range_end,
            "issues_summary": self.issues_summary[:50],  # capped for report size
            "created_at": self.created_at,
        }

    def to_markdown(self) -> str:
        md = [
            f"# Data Quality Audit Report: {self.instrument} ({self.source})",
            f"- **Generated:** {self.created_at}",
            f"- **Quality Score:** {self.quality_score:.2f} / 100.0",
            f"- **Date Range:** {self.date_range_start} to {self.date_range_end}",
            f"- **Total Input Bars:** {self.total_input_candles} | **Valid Cleaned Bars:** {self.valid_candles}",
            "",
            "## Metric Checks Breakdown",
            f"| Inspection Check | Discrepancies Found |",
            f"|---|---|",
            f"| Duplicate Timestamps | {self.duplicate_timestamps} |",
            f"| Out-of-Order Timestamps | {self.out_of_order_timestamps} |",
            f"| Invalid OHLC Relationships | {self.invalid_ohlc_bars} |",
            f"| Negative/Zero Volumes | {self.zero_negative_volumes} |",
            f"| Abnormal Price Spikes (>5σ) | {self.abnormal_price_spikes} |",
            f"| Time Gaps Detected | {self.gaps_detected} |",
            "",
        ]
        if self.issues_summary:
            md.append("## Sample Issues Log (First 10)")
            for issue in self.issues_summary[:10]:
                md.append(f"- **{issue.get('type')}** at `{issue.get('timestamp')}`: {issue.get('description')}")
        else:
            md.append("✅ **Zero critical data corruptions detected.**")
        return "\n".join(md)


class DataQualityEngine:
    """Rigorous quantitative data validation suite."""

    def __init__(self, reports_dir: Optional[Path] = None):
        self.reports_dir = reports_dir or settings.QUALITY_REPORTS_DIR
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def validate_and_clean(
        self,
        df: pd.DataFrame,
        instrument: str = "XAUUSD",
        source: str = "UNKNOWN",
        expected_frequency_seconds: Optional[int] = None,
        save_report: bool = True,
    ) -> Tuple[pd.DataFrame, QualityReport]:
        """Validate candle DataFrame, produce an audit report, and return clean data."""
        if df.empty:
            rep = QualityReport(
                instrument=instrument,
                source=source,
                total_input_candles=0,
                valid_candles=0,
                quality_score=0.0,
                duplicate_timestamps=0,
                out_of_order_timestamps=0,
                invalid_ohlc_bars=0,
                zero_negative_volumes=0,
                abnormal_price_spikes=0,
                gaps_detected=0,
            )
            return df, rep

        work_df = df.copy()
        total_input = len(work_df)
        issues = []

        # 1. Monotonic Timestamp & Duplicate Check
        duplicate_ts_mask = work_df["timestamp"].duplicated(keep="first")
        duplicate_count = int(duplicate_ts_mask.sum())
        if duplicate_count > 0:
            for ts in work_df.loc[duplicate_ts_mask, "timestamp"].head(10):
                issues.append({
                    "type": "DUPLICATE_TIMESTAMP",
                    "timestamp": str(ts),
                    "description": "Identical timestamp candle found and deduplicated.",
                })
            work_df = work_df.drop_duplicates(subset=["timestamp"], keep="first")

        # 2. Out-of-Order Timestamps
        is_monotonic = work_df["timestamp"].is_monotonic_increasing
        out_of_order_count = 0
        if not is_monotonic:
            diffs = work_df["timestamp"].diff()
            out_of_order_count = int((diffs < pd.Timedelta(0)).sum())
            issues.append({
                "type": "OUT_OF_ORDER_TIMESTAMPS",
                "timestamp": str(work_df["timestamp"].iloc[0]),
                "description": f"Found {out_of_order_count} non-chronological candles. Sorting strictly by UTC timestamp.",
            })
            work_df = work_df.sort_values("timestamp").reset_index(drop=True)

        # 3. Invalid OHLC Relationships
        # High >= Low, High >= Open, High >= Close, Low <= Open, Low <= Close, prices > 0
        invalid_ohlc_mask = (
            (work_df["high"] < work_df["low"]) |
            (work_df["high"] < work_df["open"]) |
            (work_df["high"] < work_df["close"]) |
            (work_df["low"] > work_df["open"]) |
            (work_df["low"] > work_df["close"]) |
            (work_df["low"] <= 0) |
            (work_df["open"] <= 0) |
            (work_df["high"] <= 0) |
            (work_df["close"] <= 0)
        )
        invalid_ohlc_count = int(invalid_ohlc_mask.sum())
        if invalid_ohlc_count > 0:
            for _, row in work_df[invalid_ohlc_mask].head(10).iterrows():
                issues.append({
                    "type": "INVALID_OHLC_GEOMETRY",
                    "timestamp": str(row["timestamp"]),
                    "description": f"O={row['open']}, H={row['high']}, L={row['low']}, C={row['close']} violates OHLC bounds.",
                })
            # Drop invalid OHLC bars rather than silently altering them
            work_df = work_df[~invalid_ohlc_mask].copy()

        # 4. Zero / Negative Volume
        zero_neg_vol_mask = work_df["volume"] <= 0
        zero_neg_vol_count = int(zero_neg_vol_mask.sum())
        if (work_df["volume"] < 0).any():
            for _, row in work_df[work_df["volume"] < 0].head(5).iterrows():
                issues.append({
                    "type": "NEGATIVE_VOLUME",
                    "timestamp": str(row["timestamp"]),
                    "description": f"Negative volume {row['volume']} reset to 0.",
                })
            work_df.loc[work_df["volume"] < 0, "volume"] = 0.0

        # 5. Abnormal Price Spikes (> 5σ relative to 20-bar volatility)
        spikes_count = 0
        if len(work_df) >= 20:
            pct_change = work_df["close"].pct_change().abs()
            rolling_std = pct_change.rolling(window=20).std().fillna(pct_change.std() or 0.001)
            spike_mask = pct_change > (rolling_std * 5.0 + 0.015)  # price jump > 5 std dev and > 1.5% in a single bar
            spikes_count = int(spike_mask.sum())
            if spikes_count > 0:
                for _, row in work_df[spike_mask].head(5).iterrows():
                    issues.append({
                        "type": "ABNORMAL_PRICE_SPIKE",
                        "timestamp": str(row["timestamp"]),
                        "description": f"Extreme price jump observed at close={row['close']}.",
                    })

        # 6. Gap Detection
        gaps_count = 0
        if expected_frequency_seconds and len(work_df) > 1:
            time_diffs = work_df["timestamp"].diff().dt.total_seconds().dropna()
            # Flag gaps > 3x expected frequency (excluding weekends > 48h)
            gap_mask = (time_diffs > (expected_frequency_seconds * 3)) & (time_diffs < 60 * 60 * 72)
            gaps_count = int(gap_mask.sum())
            if gaps_count > 0:
                issues.append({
                    "type": "TIME_GAPS_DETECTED",
                    "timestamp": str(work_df["timestamp"].iloc[0]),
                    "description": f"Detected {gaps_count} unexplained interval gaps.",
                })

        # Scoring Formula:
        # Base 100, penalized by invalid bars, duplicates, and corruptions
        penalty = (
            (duplicate_count / max(total_input, 1)) * 30.0 +
            (invalid_ohlc_count / max(total_input, 1)) * 50.0 +
            (out_of_order_count / max(total_input, 1)) * 20.0 +
            (spikes_count / max(total_input, 1)) * 10.0
        )
        quality_score = max(0.0, min(100.0, 100.0 - penalty))

        start_str = str(work_df["timestamp"].iloc[0]) if not work_df.empty else None
        end_str = str(work_df["timestamp"].iloc[-1]) if not work_df.empty else None

        report = QualityReport(
            instrument=instrument,
            source=source,
            total_input_candles=total_input,
            valid_candles=len(work_df),
            quality_score=quality_score,
            duplicate_timestamps=duplicate_count,
            out_of_order_timestamps=out_of_order_count,
            invalid_ohlc_bars=invalid_ohlc_count,
            zero_negative_volumes=zero_neg_vol_count,
            abnormal_price_spikes=spikes_count,
            gaps_detected=gaps_count,
            date_range_start=start_str,
            date_range_end=end_str,
            issues_summary=issues,
        )

        if save_report:
            self._save_report(report)

        return work_df.reset_index(drop=True), report

    def _save_report(self, report: QualityReport) -> None:
        """Persist JSON and Markdown quality audit reports."""
        ts_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_sym = report.instrument.replace("=", "_")
        json_path = self.reports_dir / f"quality_{safe_sym}_{report.source}_{ts_slug}.json"
        md_path = self.reports_dir / f"quality_{safe_sym}_{report.source}_{ts_slug}.md"

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, indent=2)

        with open(md_path, "w", encoding="utf-8") as f:
            f.write(report.to_markdown())

        logger.info(f"Saved data quality report to {json_path} (Score: {report.quality_score:.1f})")
