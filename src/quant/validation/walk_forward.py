"""Walk-Forward Analysis (WFA) and Walk-Forward Efficiency (WFE) verification engine."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from src.backtest.engine import BacktestEngine
from src.backtest.types import BacktestConfig, BacktestResult
from src.core.logger import logger
from src.quant.validation.config import DEFAULT_VALIDATION_CONFIG, ValidationThresholdsConfig
from src.strategy.base import BaseStrategy


def calculate_wfe(is_return: float, oos_return: float, oos_trades: int) -> float:
    """Calculate Walk-Forward Efficiency (WFE) as a decimal ratio in [0.0, inf).

    MATHEMATICAL EDGE CASE INVARIANTS:
    1. If is_return <= 0.0 -> WFE = 0.0000 (FAIL)
    2. If oos_return <= 0.0 -> WFE = 0.0000 (FAIL)
    3. If oos_trades == 0 -> WFE = 0.0000 (FAIL)
    4. If is_return or oos_return is NaN or Inf -> WFE = 0.0000 (FAIL)
    """
    if np.isnan(is_return) or np.isinf(is_return) or np.isnan(oos_return) or np.isinf(oos_return):
        return 0.0000

    if is_return <= 0.0:
        return 0.0000

    if oos_return <= 0.0 or oos_trades == 0:
        return 0.0000

    raw_wfe = oos_return / is_return
    if np.isnan(raw_wfe) or np.isinf(raw_wfe):
        return 0.0000

    return round(float(raw_wfe), 4)


@dataclass
class WalkForwardFold:
    """Single Walk-Forward In-Sample to Out-of-Sample window slice."""
    fold_index: int
    is_start: datetime
    is_end: datetime
    oos_start: datetime
    oos_end: datetime
    is_return: float
    oos_return: float
    is_profit_factor: float
    oos_profit_factor: float
    is_trades: int
    oos_trades: int
    wfe: float
    passed: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fold_index": self.fold_index,
            "is_start": self.is_start.isoformat(),
            "is_end": self.is_end.isoformat(),
            "oos_start": self.oos_start.isoformat(),
            "oos_end": self.oos_end.isoformat(),
            "is_return": round(self.is_return, 4),
            "oos_return": round(self.oos_return, 4),
            "is_profit_factor": round(self.is_profit_factor, 4),
            "oos_profit_factor": round(self.oos_profit_factor, 4),
            "is_trades": self.is_trades,
            "oos_trades": self.oos_trades,
            "wfe": self.wfe,
            "passed": self.passed,
        }


@dataclass
class WalkForwardAnalysisResult:
    """Aggregate result across all Walk-Forward folds."""
    folds: List[WalkForwardFold] = field(default_factory=list)
    mean_wfe: float = 0.0000
    median_wfe: float = 0.0000
    consistency_rate: float = 0.0
    oos_retention_ratio: float = 0.0
    passed: bool = False
    rejection_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "folds_count": len(self.folds),
            "mean_wfe": self.mean_wfe,
            "median_wfe": self.median_wfe,
            "consistency_rate": round(self.consistency_rate, 4),
            "oos_retention_ratio": round(self.oos_retention_ratio, 4),
            "passed": self.passed,
            "rejection_reasons": self.rejection_reasons,
            "folds": [f.to_dict() for f in self.folds],
        }


class WalkForwardAnalyzer:
    """Deterministic Rolling Walk-Forward Analysis engine."""

    def __init__(
        self,
        config: Optional[ValidationThresholdsConfig] = None,
        backtest_config: Optional[BacktestConfig] = None,
    ):
        self.config = config or DEFAULT_VALIDATION_CONFIG
        self.backtest_config = backtest_config or BacktestConfig()
        self.engine = BacktestEngine(self.backtest_config)

    def partition_folds(
        self,
        df: pd.DataFrame,
        is_months: int = 12,
        oos_months: int = 3,
        step_months: int = 3,
        min_fold_days: int = 60,
    ) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
        """Slice historical DataFrame into rolling chronological IS and OOS partitions."""
        if df.empty:
            return []

        df_sorted = df.sort_values("timestamp").reset_index(drop=True)
        start_ts = df_sorted["timestamp"].min()
        end_ts = df_sorted["timestamp"].max()

        folds_data: List[Tuple[pd.DataFrame, pd.DataFrame]] = []
        current_is_start = start_ts

        while True:
            current_is_end = current_is_start + timedelta(days=is_months * 30)
            current_oos_start = current_is_end
            current_oos_end = current_oos_start + timedelta(days=oos_months * 30)

            if current_oos_end > end_ts:
                break

            # Filter slices
            is_df = df_sorted[
                (df_sorted["timestamp"] >= current_is_start) & (df_sorted["timestamp"] < current_is_end)
            ]
            oos_df = df_sorted[
                (df_sorted["timestamp"] >= current_oos_start) & (df_sorted["timestamp"] < current_oos_end)
            ]

            # Verify minimum 60-day constraint
            is_duration_days = (current_is_end - current_is_start).days
            oos_duration_days = (current_oos_end - current_oos_start).days

            if is_duration_days >= min_fold_days and oos_duration_days >= min_fold_days:
                if len(is_df) > 50 and len(oos_df) > 50:
                    folds_data.append((is_df, oos_df))

            # Step forward
            current_is_start += timedelta(days=step_months * 30)

        return folds_data

    def evaluate_strategy(
        self,
        df: pd.DataFrame,
        strategy: BaseStrategy,
        instrument: str = "XAUUSD",
        timeframe: str = "1h",
    ) -> WalkForwardAnalysisResult:
        """Execute rolling Walk-Forward validation on strategy."""
        folds_partitions = self.partition_folds(df)

        if not folds_partitions:
            min_days = (df["timestamp"].max() - df["timestamp"].min()).days if not df.empty else 0
            return WalkForwardAnalysisResult(
                passed=False,
                rejection_reasons=[
                    f"Insufficient historical data duration ({min_days} days) to construct rolling Walk-Forward folds (requires minimum 15 months / 450 days with >= 60-day folds)."
                ],
            )

        folds: List[WalkForwardFold] = []
        rejection_reasons: List[str] = []

        total_is_gpf = 0.0
        total_oos_gpf = 0.0

        for i, (is_df, oos_df) in enumerate(folds_partitions):
            # Run deterministic backtest on In-Sample
            is_res = self.engine.run(is_df, strategy, instrument, timeframe)
            # Run deterministic backtest on Out-of-Sample
            oos_res = self.engine.run(oos_df, strategy, instrument, timeframe)

            is_ret = is_res.metrics.total_return_pct
            oos_ret = oos_res.metrics.total_return_pct
            is_trades = is_res.metrics.total_trades
            oos_trades = oos_res.metrics.total_trades
            is_gpf = is_res.metrics.profit_factor
            oos_gpf = oos_res.metrics.profit_factor

            total_is_gpf += is_gpf
            total_oos_gpf += oos_gpf

            fold_wfe = calculate_wfe(is_ret, oos_ret, oos_trades)
            fold_passed = fold_wfe >= self.config.MIN_WFE_RATIO and oos_trades >= 15

            folds.append(
                WalkForwardFold(
                    fold_index=i + 1,
                    is_start=is_df["timestamp"].min(),
                    is_end=is_df["timestamp"].max(),
                    oos_start=oos_df["timestamp"].min(),
                    oos_end=oos_df["timestamp"].max(),
                    is_return=is_ret,
                    oos_return=oos_ret,
                    is_profit_factor=is_gpf,
                    oos_profit_factor=oos_gpf,
                    is_trades=is_trades,
                    oos_trades=oos_trades,
                    wfe=fold_wfe,
                    passed=fold_passed,
                )
            )

        wfe_values = [f.wfe for f in folds]
        mean_wfe = round(float(np.mean(wfe_values)), 4) if wfe_values else 0.0000
        median_wfe = round(float(np.median(wfe_values)), 4) if wfe_values else 0.0000
        passing_folds = sum(1 for f in folds if f.passed)
        consistency_rate = round(passing_folds / len(folds), 4) if folds else 0.0

        avg_is_gpf = total_is_gpf / len(folds) if folds else 1.0
        avg_oos_gpf = total_oos_gpf / len(folds) if folds else 0.0
        oos_retention = round(avg_oos_gpf / max(avg_is_gpf, 0.01), 4)

        overall_passed = True

        if mean_wfe < self.config.MIN_WFE_RATIO:
            overall_passed = False
            rejection_reasons.append(
                f"Mean WFE {mean_wfe:.4f} below institutional minimum {self.config.MIN_WFE_RATIO:.4f}."
            )

        if oos_retention < self.config.MIN_OOS_RETENTION_RATIO:
            overall_passed = False
            rejection_reasons.append(
                f"OOS profit factor retention ratio {oos_retention:.4f} below minimum {self.config.MIN_OOS_RETENTION_RATIO:.4f}."
            )

        if consistency_rate < 0.50:
            overall_passed = False
            rejection_reasons.append(
                f"Walk-Forward fold consistency rate {consistency_rate:.2f} below minimum 0.50."
            )

        return WalkForwardAnalysisResult(
            folds=folds,
            mean_wfe=mean_wfe,
            median_wfe=median_wfe,
            consistency_rate=consistency_rate,
            oos_retention_ratio=oos_retention,
            passed=overall_passed,
            rejection_reasons=rejection_reasons,
        )

