"""Backtest vs Paper Parity Tracker.

Evaluates execution fidelity across 11 canonical dimensions according to Section 20
of docs/PHASE_3_DESIGN_REPORT.md.
"""

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Dict, List, Optional


class ParityStatus(str, Enum):
    """Deterministic Parity Classifications."""

    PARITY_PASS = "PARITY_PASS"
    PARITY_WARNING = "PARITY_WARNING"
    PARITY_FAILED = "PARITY_FAILED"


@dataclass
class ParityDimensionResult:
    """Result of an individual parity dimension evaluation."""

    name: str
    dimension_index: int
    passed: bool
    status: ParityStatus
    measured_value: float
    threshold_value: float
    unit: str
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "dimension_index": self.dimension_index,
            "passed": self.passed,
            "status": self.status.value,
            "measured_value": round(self.measured_value, 4),
            "threshold_value": round(self.threshold_value, 4),
            "unit": self.unit,
            "details": self.details,
        }


@dataclass
class ParityReport:
    """Consolidated 11-dimension Backtest vs Paper parity report."""

    strategy_id: str
    overall_status: ParityStatus
    is_promotable: bool
    total_backtest_trades: int
    total_paper_trades: int
    dimensions: List[ParityDimensionResult] = field(default_factory=list)
    slippage_drift_pct: float = 0.0
    pnl_tracking_error_pct: float = 0.0
    signal_congruence_pct: float = 100.0
    trade_count_deviation_pct: float = 0.0
    disqualification_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "overall_status": self.overall_status.value,
            "is_promotable": self.is_promotable,
            "total_backtest_trades": self.total_backtest_trades,
            "total_paper_trades": self.total_paper_trades,
            "slippage_drift_pct": round(self.slippage_drift_pct, 4),
            "pnl_tracking_error_pct": round(self.pnl_tracking_error_pct, 4),
            "signal_congruence_pct": round(self.signal_congruence_pct, 4),
            "trade_count_deviation_pct": round(self.trade_count_deviation_pct, 4),
            "disqualification_reasons": self.disqualification_reasons,
            "dimensions": [d.to_dict() for d in self.dimensions],
        }


class BacktestPaperParityTracker:
    """Evaluates backtest vs. paper execution fidelity across 11 canonical dimensions."""

    def __init__(
        self,
        max_entry_price_dev_pct: float = 0.5,
        max_exit_price_dev_pct: float = 0.5,
        max_slippage_drift_usd: float = 0.50,
        max_fee_model_dev_pct: float = 1.0,
    ) -> None:
        self.max_entry_price_dev_pct = max_entry_price_dev_pct
        self.max_exit_price_dev_pct = max_exit_price_dev_pct
        self.max_slippage_drift_usd = max_slippage_drift_usd
        self.max_fee_model_dev_pct = max_fee_model_dev_pct

    def evaluate(
        self,
        strategy_id: str,
        backtest_trades: List[Dict[str, Any]],
        paper_trades: List[Dict[str, Any]],
    ) -> ParityReport:
        """Evaluate parity across all 11 dimensions.

        Args:
            strategy_id: Unique strategy identifier.
            backtest_trades: Canonical simulated trades from Phase 2 backtest.
            paper_trades: Executed virtual trades from Phase 3 paper trading engine.

        Returns:
            ParityReport with deterministic PASS/WARNING/FAILED status.
        """
        n_bt = len(backtest_trades)
        n_paper = len(paper_trades)
        dimensions: List[ParityDimensionResult] = []
        disqualifications: List[str] = []

        if n_bt == 0 and n_paper == 0:
            names = [
                ("Signal Timestamp Congruence", 100.0, "%"),
                ("Signal Direction Congruence", 100.0, "%"),
                ("Entry Timestamp Congruence", 100.0, "%"),
                ("Entry Price Deviation", 0.0, "%"),
                ("Exit Timestamp Congruence", 100.0, "%"),
                ("Exit Price Deviation", 0.0, "%"),
                ("Quantity Match", 100.0, "%"),
                ("Fee Model Alignment", 0.0, "%"),
                ("Slippage Drift", 0.0, "%"),
                ("Realized P&L Divergence", 0.0, "%"),
                ("Trade Count Match", 0.0, "%"),
            ]
            default_dims = [
                ParityDimensionResult(
                    name=name,
                    dimension_index=idx + 1,
                    passed=True,
                    status=ParityStatus.PARITY_PASS,
                    measured_value=val,
                    threshold_value=val,
                    unit=unit,
                )
                for idx, (name, val, unit) in enumerate(names)
            ]
            return ParityReport(
                strategy_id=strategy_id,
                overall_status=ParityStatus.PARITY_PASS,
                is_promotable=True,
                total_backtest_trades=0,
                total_paper_trades=0,
                dimensions=default_dims,
            )


        # Dimension 11: Trade Count Match
        tc_dev_pct = abs(n_paper - n_bt) / max(n_bt, 1) * 100.0
        tc_passed = tc_dev_pct == 0.0
        tc_status = (
            ParityStatus.PARITY_PASS
            if tc_dev_pct == 0.0
            else (ParityStatus.PARITY_WARNING if tc_dev_pct <= 5.0 else ParityStatus.PARITY_FAILED)
        )
        if tc_status == ParityStatus.PARITY_FAILED:
            disqualifications.append(f"Trade count deviation {tc_dev_pct:.2f}% exceeds 5.0%")

        # Match trades by index or timestamp up to min count
        min_n = min(n_bt, n_paper)
        signal_time_matches = 0
        signal_dir_matches = 0
        entry_time_matches = 0
        exit_time_matches = 0
        qty_matches = 0

        entry_price_devs: List[float] = []
        exit_price_devs: List[float] = []
        fee_devs: List[float] = []
        bt_slippages: List[float] = []
        paper_slippages: List[float] = []
        bt_pnls: List[float] = []
        paper_pnls: List[float] = []

        for i in range(min_n):
            bt = backtest_trades[i]
            pt = paper_trades[i]

            # 1. Signal timestamp
            if str(bt.get("signal_time")) == str(pt.get("signal_time")):
                signal_time_matches += 1

            # 2. Signal direction
            if str(bt.get("signal_direction", "")).upper() == str(pt.get("signal_direction", "")).upper():
                signal_dir_matches += 1

            # 3. Entry timestamp (Next-bar open)
            if str(bt.get("entry_time")) == str(pt.get("entry_time")):
                entry_time_matches += 1

            # 4. Entry price deviation
            bt_ep = float(bt.get("entry_price", 0.0))
            pt_ep = float(pt.get("entry_price", 0.0))
            if bt_ep > 0:
                entry_price_devs.append(abs(pt_ep - bt_ep) / bt_ep * 100.0)

            # 5. Exit timestamp
            if str(bt.get("exit_time")) == str(pt.get("exit_time")):
                exit_time_matches += 1

            # 6. Exit price deviation
            bt_xp = float(bt.get("exit_price", 0.0))
            pt_xp = float(pt.get("exit_price", 0.0))
            if bt_xp > 0:
                exit_price_devs.append(abs(pt_xp - bt_xp) / bt_xp * 100.0)

            # 7. Quantity match
            if math.isclose(float(bt.get("quantity", 0.0)), float(pt.get("quantity", 0.0)), rel_tol=1e-4):
                qty_matches += 1

            # 8. Fee model alignment
            bt_fee = float(bt.get("fee", 0.0))
            pt_fee = float(pt.get("fee", 0.0))
            if bt_fee > 0:
                fee_devs.append(abs(pt_fee - bt_fee) / bt_fee * 100.0)

            # 9. Slippage
            bt_slippages.append(float(bt.get("slippage", 0.0)))
            paper_slippages.append(float(pt.get("slippage", 0.0)))

            # 10. Realized PnL
            bt_pnls.append(float(bt.get("pnl", 0.0)))
            paper_pnls.append(float(pt.get("pnl", 0.0)))

        # Rates & averages
        denom = max(min_n, 1)
        sig_time_pct = (signal_time_matches / denom) * 100.0
        sig_dir_pct = (signal_dir_matches / denom) * 100.0
        entry_time_pct = (entry_time_matches / denom) * 100.0
        exit_time_pct = (exit_time_matches / denom) * 100.0
        qty_match_pct = (qty_matches / denom) * 100.0

        avg_entry_dev = sum(entry_price_devs) / max(len(entry_price_devs), 1)
        avg_exit_dev = sum(exit_price_devs) / max(len(exit_price_devs), 1)
        avg_fee_dev = sum(fee_devs) / max(len(fee_devs), 1)

        # Slippage drift
        avg_bt_slip = sum(bt_slippages) / max(len(bt_slippages), 1)
        avg_paper_slip = sum(paper_slippages) / max(len(paper_slippages), 1)
        slip_drift_usd = abs(avg_paper_slip - avg_bt_slip)
        slip_drift_pct = (
            abs(avg_paper_slip - avg_bt_slip) / max(abs(avg_bt_slip), 0.01) * 100.0
            if avg_bt_slip > 0
            else (100.0 if avg_paper_slip > 0 else 0.0)
        )

        # Realized PnL Divergence (tracking error %)
        total_bt_pnl = sum(bt_pnls)
        total_paper_pnl = sum(paper_pnls)
        pnl_tracking_error_pct = (
            abs(total_paper_pnl - total_bt_pnl) / max(abs(total_bt_pnl), 1.0) * 100.0
        )

        # Dimension 1: Signal Timestamp Congruence (100% required)
        dim1_pass = sig_time_pct == 100.0
        dim1_status = ParityStatus.PARITY_PASS if dim1_pass else ParityStatus.PARITY_FAILED
        if not dim1_pass:
            disqualifications.append(f"Signal timestamp congruence {sig_time_pct:.1f}% != 100%")
        dimensions.append(
            ParityDimensionResult(
                name="Signal Timestamp Congruence",
                dimension_index=1,
                passed=dim1_pass,
                status=dim1_status,
                measured_value=sig_time_pct,
                threshold_value=100.0,
                unit="%",
            )
        )

        # Dimension 2: Signal Direction Congruence (100% required)
        dim2_pass = sig_dir_pct == 100.0
        dim2_status = ParityStatus.PARITY_PASS if dim2_pass else ParityStatus.PARITY_FAILED
        if not dim2_pass:
            disqualifications.append(f"Signal direction congruence {sig_dir_pct:.1f}% != 100%")
        dimensions.append(
            ParityDimensionResult(
                name="Signal Direction Congruence",
                dimension_index=2,
                passed=dim2_pass,
                status=dim2_status,
                measured_value=sig_dir_pct,
                threshold_value=100.0,
                unit="%",
            )
        )

        # Dimension 3: Entry Timestamp Congruence (Next-Bar Open 100% match)
        dim3_pass = entry_time_pct == 100.0
        dim3_status = ParityStatus.PARITY_PASS if dim3_pass else ParityStatus.PARITY_FAILED
        if not dim3_pass:
            disqualifications.append(f"Entry timestamp congruence {entry_time_pct:.1f}% != 100%")
        dimensions.append(
            ParityDimensionResult(
                name="Entry Timestamp Congruence",
                dimension_index=3,
                passed=dim3_pass,
                status=dim3_status,
                measured_value=entry_time_pct,
                threshold_value=100.0,
                unit="%",
            )
        )

        # Dimension 4: Entry Price Deviation
        dim4_pass = avg_entry_dev <= self.max_entry_price_dev_pct
        dim4_status = ParityStatus.PARITY_PASS if dim4_pass else ParityStatus.PARITY_WARNING
        dimensions.append(
            ParityDimensionResult(
                name="Entry Price Deviation",
                dimension_index=4,
                passed=dim4_pass,
                status=dim4_status,
                measured_value=avg_entry_dev,
                threshold_value=self.max_entry_price_dev_pct,
                unit="%",
            )
        )

        # Dimension 5: Exit Timestamp Congruence
        dim5_pass = exit_time_pct == 100.0
        dim5_status = ParityStatus.PARITY_PASS if dim5_pass else ParityStatus.PARITY_WARNING
        dimensions.append(
            ParityDimensionResult(
                name="Exit Timestamp Congruence",
                dimension_index=5,
                passed=dim5_pass,
                status=dim5_status,
                measured_value=exit_time_pct,
                threshold_value=100.0,
                unit="%",
            )
        )

        # Dimension 6: Exit Price Deviation
        dim6_pass = avg_exit_dev <= self.max_exit_price_dev_pct
        dim6_status = ParityStatus.PARITY_PASS if dim6_pass else ParityStatus.PARITY_WARNING
        dimensions.append(
            ParityDimensionResult(
                name="Exit Price Deviation",
                dimension_index=6,
                passed=dim6_pass,
                status=dim6_status,
                measured_value=avg_exit_dev,
                threshold_value=self.max_exit_price_dev_pct,
                unit="%",
            )
        )

        # Dimension 7: Quantity Match
        dim7_pass = qty_match_pct == 100.0
        dim7_status = ParityStatus.PARITY_PASS if dim7_pass else ParityStatus.PARITY_FAILED
        if not dim7_pass:
            disqualifications.append(f"Quantity match {qty_match_pct:.1f}% != 100%")
        dimensions.append(
            ParityDimensionResult(
                name="Quantity Match",
                dimension_index=7,
                passed=dim7_pass,
                status=dim7_status,
                measured_value=qty_match_pct,
                threshold_value=100.0,
                unit="%",
            )
        )

        # Dimension 8: Fee Model Alignment
        dim8_pass = avg_fee_dev <= self.max_fee_model_dev_pct
        dim8_status = ParityStatus.PARITY_PASS if dim8_pass else ParityStatus.PARITY_WARNING
        dimensions.append(
            ParityDimensionResult(
                name="Fee Model Alignment",
                dimension_index=8,
                passed=dim8_pass,
                status=dim8_status,
                measured_value=avg_fee_dev,
                threshold_value=self.max_fee_model_dev_pct,
                unit="%",
            )
        )

        # Dimension 9: Slippage Drift
        dim9_status = (
            ParityStatus.PARITY_PASS
            if (slip_drift_pct <= 20.0 and slip_drift_usd <= self.max_slippage_drift_usd)
            else (ParityStatus.PARITY_WARNING if slip_drift_pct <= 50.0 else ParityStatus.PARITY_FAILED)
        )
        if dim9_status == ParityStatus.PARITY_FAILED:
            disqualifications.append(f"Slippage drift {slip_drift_pct:.1f}% exceeds 50.0%")
        dimensions.append(
            ParityDimensionResult(
                name="Slippage Drift",
                dimension_index=9,
                passed=(dim9_status != ParityStatus.PARITY_FAILED),
                status=dim9_status,
                measured_value=slip_drift_pct,
                threshold_value=20.0,
                unit="%",
                details=f"USD diff: {slip_drift_usd:.4f}",
            )
        )

        # Dimension 10: Realized P&L Divergence
        dim10_status = (
            ParityStatus.PARITY_PASS
            if pnl_tracking_error_pct <= 2.0
            else (ParityStatus.PARITY_WARNING if pnl_tracking_error_pct <= 5.0 else ParityStatus.PARITY_FAILED)
        )
        if dim10_status == ParityStatus.PARITY_FAILED:
            disqualifications.append(f"P&L tracking error {pnl_tracking_error_pct:.2f}% exceeds 5.0%")
        dimensions.append(
            ParityDimensionResult(
                name="Realized P&L Divergence",
                dimension_index=10,
                passed=(dim10_status != ParityStatus.PARITY_FAILED),
                status=dim10_status,
                measured_value=pnl_tracking_error_pct,
                threshold_value=2.0,
                unit="%",
            )
        )

        # Add Dimension 11 to dimensions list
        dimensions.append(
            ParityDimensionResult(
                name="Trade Count Match",
                dimension_index=11,
                passed=tc_passed,
                status=tc_status,
                measured_value=tc_dev_pct,
                threshold_value=0.0,
                unit="%",
            )
        )

        # Overall Status Classification:
        # PARITY_PASS: slippage drift <= 20%, 100% signal congruence, P&L error <= 2%, trade count dev 0%
        # PARITY_WARNING: slippage drift 20-50%, tracking error 2-5%, trade count dev <= 5%
        # PARITY_FAILED: slippage drift > 50%, signal mismatch > 0%, tracking error > 5%, trade count dev > 5%
        if (
            slip_drift_pct > 50.0
            or sig_dir_pct < 100.0
            or sig_time_pct < 100.0
            or entry_time_pct < 100.0
            or pnl_tracking_error_pct > 5.0
            or tc_dev_pct > 5.0
            or qty_match_pct < 100.0
        ):
            overall = ParityStatus.PARITY_FAILED
        elif (
            slip_drift_pct > 20.0
            or pnl_tracking_error_pct > 2.0
            or tc_dev_pct > 0.0
            or not dim4_pass
            or not dim5_pass
            or not dim6_pass
            or not dim8_pass
        ):
            overall = ParityStatus.PARITY_WARNING
        else:
            overall = ParityStatus.PARITY_PASS

        is_promotable = overall == ParityStatus.PARITY_PASS and len(disqualifications) == 0

        return ParityReport(
            strategy_id=strategy_id,
            overall_status=overall,
            is_promotable=is_promotable,
            total_backtest_trades=n_bt,
            total_paper_trades=n_paper,
            dimensions=dimensions,
            slippage_drift_pct=slip_drift_pct,
            pnl_tracking_error_pct=pnl_tracking_error_pct,
            signal_congruence_pct=sig_dir_pct,
            trade_count_deviation_pct=tc_dev_pct,
            disqualification_reasons=disqualifications,
        )
