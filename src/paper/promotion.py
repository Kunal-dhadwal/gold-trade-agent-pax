"""Deterministic Paper-to-Live Promotion Evaluator.

Enforces Dual-Stage Additive Gates according to Section 19 of docs/PHASE_3_DESIGN_REPORT.md.
AI models have ZERO authority to promote strategies or initiate live candidacy.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import hmac
from typing import Any, Dict, List, Optional

from src.core.config import settings
from src.paper.parity import ParityReport, ParityStatus


class GateStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass
class PromotionGateResult:
    name: str
    stage: int  # 1 (Research/Backtest) or 2 (Realized Paper)
    passed: bool
    measured_value: float
    threshold_value: float
    unit: str
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "stage": self.stage,
            "passed": self.passed,
            "measured_value": round(self.measured_value, 4),
            "threshold_value": round(self.threshold_value, 4),
            "unit": self.unit,
            "details": self.details,
        }


@dataclass
class PromotionEvaluationReport:
    strategy_id: str
    stage1_passed: bool
    stage2_passed: bool
    eligible_for_live_candidate: bool
    evaluated_at: str
    gate_results: List[PromotionGateResult] = field(default_factory=list)
    disqualifications: List[str] = field(default_factory=list)
    operator_sign_off_status: str = "PENDING_HUMAN_SIGN_OFF"
    signed_by: Optional[str] = None
    signature: Optional[str] = None
    signed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "stage1_passed": self.stage1_passed,
            "stage2_passed": self.stage2_passed,
            "eligible_for_live_candidate": self.eligible_for_live_candidate,
            "evaluated_at": self.evaluated_at,
            "disqualifications": self.disqualifications,
            "operator_sign_off_status": self.operator_sign_off_status,
            "signed_by": self.signed_by,
            "signature": self.signature,
            "signed_at": self.signed_at,
            "gate_results": [g.to_dict() for g in self.gate_results],
        }


# Nonce replay prevention cache
_USED_SIGN_NONCES: set[str] = set()


def verify_promotion_signature(
    strategy_id: str,
    operator_id: str,
    timestamp_iso: str,
    nonce: str,
    signature: str,
    operator_secret: str,
) -> bool:
    """Cryptographically verify an operator promotion digital signature with replay & expiry checks."""
    # 1. Identity validation
    if operator_id.lower().startswith(("ai_", "bot_", "agent_", "llm_")):
        raise PermissionError("SECURITY VETO: AI models possess zero platform authority.")

    expected_secret = settings.OPERATOR_SIGNING_KEYS.get(operator_id)
    if not expected_secret or not hmac.compare_digest(operator_secret, expected_secret):
        return False

    # 2. Expiry window check (max 5 minutes / 300 seconds)
    try:
        ts = datetime.fromisoformat(timestamp_iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age_seconds = abs((now - ts).total_seconds())
        if age_seconds > 300.0:
            logger.warning(f"Signature expired: timestamp age {age_seconds:.1f}s > 300s.")
            return False
    except Exception as e:
        logger.warning(f"Invalid signature timestamp format '{timestamp_iso}': {e}")
        return False

    # 3. Nonce replay check
    if nonce in _USED_SIGN_NONCES:
        logger.warning(f"Replay attack detected: nonce '{nonce}' has already been consumed.")
        return False

    # 4. Canonical payload & HMAC comparison
    payload = f"PROMOTION:{strategy_id}:{operator_id}:{timestamp_iso}:{nonce}"
    expected_sig = hmac.new(
        expected_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_sig):
        return False

    # Consume nonce to prevent replay
    _USED_SIGN_NONCES.add(nonce)
    return True


class PaperPromotionEvaluator:
    """Evaluates whether a paper trading strategy qualifies for LIVE_CANDIDATE status.
    
    CRITICAL TENET:
    AI models possess ZERO authority to promote strategies, adjust risk limits, or initiate
    live candidacy. Only authorized human operators can provide cryptographic sign-off.
    """

    def __init__(
        self,
        min_paper_weeks: int = settings.PAPER_PROMOTION_MIN_WEEKS,
        min_paper_trades: int = settings.PAPER_PROMOTION_MIN_TRADES,
        min_expectancy: float = settings.PAPER_PROMOTION_MIN_EXPECTANCY,
        min_profit_factor: float = settings.PAPER_PROMOTION_MIN_PROFIT_FACTOR,
        max_drawdown_pct: float = settings.PAPER_PROMOTION_MAX_DRAWDOWN_PCT,
        max_tracking_error_pct: float = settings.PAPER_PROMOTION_MAX_TRACKING_ERROR_PCT,
    ) -> None:
        self.min_paper_weeks = min_paper_weeks
        self.min_paper_trades = min_paper_trades
        self.min_expectancy = min_expectancy
        self.min_profit_factor = min_profit_factor
        self.max_drawdown_pct = max_drawdown_pct
        self.max_tracking_error_pct = max_tracking_error_pct

    def evaluate(
        self,
        strategy_id: str,
        stage1_metrics: Dict[str, Any],
        paper_metrics: Dict[str, Any],
        parity_report: ParityReport,
        risk_incidents: int = 0,
    ) -> PromotionEvaluationReport:
        """Evaluate both Stage 1 (Phase 2 validation) and Stage 2 (Phase 3 paper execution) gates.

        Args:
            strategy_id: Strategy identifier.
            stage1_metrics: Historical validation gates (min_trades, wfe, ruin_prob, css, holdout_sharpe).
            paper_metrics: Realized paper stats (weeks, trade_count, expectancy, profit_factor, max_dd_pct).
            parity_report: Backtest vs Paper parity report.
            risk_incidents: Count of Gate 1 (DD) or Gate 8 (Kill Switch) breaches during paper phase.
        """
        gate_results: List[PromotionGateResult] = []
        disqualifications: List[str] = []

        # ================= STAGE 1: HISTORICAL RESEARCH GATES =================
        # 1.1 Minimum historical trades (N >= 50)
        s1_n = float(stage1_metrics.get("trade_count", 0))
        g1_1 = s1_n >= 50
        if not g1_1:
            disqualifications.append(f"Stage 1 Minimum trades {s1_n:.0f} < 50")
        gate_results.append(
            PromotionGateResult(
                name="Stage 1: Minimum Historical Trades",
                stage=1,
                passed=g1_1,
                measured_value=s1_n,
                threshold_value=50.0,
                unit="trades",
            )
        )

        # 1.2 Walk-Forward Efficiency (WFE >= 50%)
        s1_wfe = float(stage1_metrics.get("wfe_pct", 0.0))
        g1_2 = s1_wfe >= 50.0
        if not g1_2:
            disqualifications.append(f"Stage 1 Walk-forward efficiency {s1_wfe:.1f}% < 50%")
        gate_results.append(
            PromotionGateResult(
                name="Stage 1: Walk-Forward Efficiency",
                stage=1,
                passed=g1_2,
                measured_value=s1_wfe,
                threshold_value=50.0,
                unit="%",
            )
        )

        # 1.3 Monte Carlo Ruin Probability (P(DD >= 20%) < 5.0%)
        s1_ruin = float(stage1_metrics.get("ruin_prob_pct", 100.0))
        g1_3 = s1_ruin < 5.0
        if not g1_3:
            disqualifications.append(f"Stage 1 Monte Carlo ruin prob {s1_ruin:.1f}% >= 5.0%")
        gate_results.append(
            PromotionGateResult(
                name="Stage 1: Monte Carlo Ruin Probability",
                stage=1,
                passed=g1_3,
                measured_value=s1_ruin,
                threshold_value=5.0,
                unit="%",
            )
        )

        # 1.4 Composite Strategy Score (CSS >= 80.0)
        s1_css = float(stage1_metrics.get("css", 0.0))
        g1_4 = s1_css >= 80.0
        if not g1_4:
            disqualifications.append(f"Stage 1 CSS {s1_css:.1f} < 80.0")
        gate_results.append(
            PromotionGateResult(
                name="Stage 1: Composite Strategy Score",
                stage=1,
                passed=g1_4,
                measured_value=s1_css,
                threshold_value=80.0,
                unit="score",
            )
        )

        # 1.5 Out-of-sample Holdout Sharpe (Sharpe >= 1.0)
        s1_sharpe = float(stage1_metrics.get("holdout_sharpe", 0.0))
        g1_5 = s1_sharpe >= 1.0
        if not g1_5:
            disqualifications.append(f"Stage 1 Holdout Sharpe {s1_sharpe:.2f} < 1.0")
        gate_results.append(
            PromotionGateResult(
                name="Stage 1: Holdout Sharpe",
                stage=1,
                passed=g1_5,
                measured_value=s1_sharpe,
                threshold_value=1.0,
                unit="ratio",
            )
        )

        stage1_passed = all([g1_1, g1_2, g1_3, g1_4, g1_5])

        # ================= STAGE 2: REALIZED PAPER TRADING GATES =================
        # 2.1 Duration (>= 4 continuous calendar weeks / 20 trading days)
        s2_weeks = float(paper_metrics.get("continuous_weeks", 0.0))
        g2_1 = s2_weeks >= self.min_paper_weeks
        if not g2_1:
            disqualifications.append(f"Stage 2 Paper duration {s2_weeks:.1f} wks < {self.min_paper_weeks} wks")
        gate_results.append(
            PromotionGateResult(
                name="Stage 2: Continuous Paper Duration",
                stage=2,
                passed=g2_1,
                measured_value=s2_weeks,
                threshold_value=float(self.min_paper_weeks),
                unit="weeks",
            )
        )

        # 2.2 Trade Count (>= 50 executed paper trades)
        s2_n = float(paper_metrics.get("trade_count", 0))
        g2_2 = s2_n >= self.min_paper_trades
        if not g2_2:
            disqualifications.append(f"Stage 2 Paper trade count {s2_n:.0f} < {self.min_paper_trades}")
        gate_results.append(
            PromotionGateResult(
                name="Stage 2: Executed Paper Trade Count",
                stage=2,
                passed=g2_2,
                measured_value=s2_n,
                threshold_value=float(self.min_paper_trades),
                unit="trades",
            )
        )

        # 2.3 Realized Expectancy (Expectancy > 0.0 USD/trade)
        s2_exp = float(paper_metrics.get("expectancy_usd", 0.0))
        g2_3 = s2_exp > self.min_expectancy
        if not g2_3:
            disqualifications.append(f"Stage 2 Realized expectancy {s2_exp:.2f} <= {self.min_expectancy}")
        gate_results.append(
            PromotionGateResult(
                name="Stage 2: Realized Trade Expectancy",
                stage=2,
                passed=g2_3,
                measured_value=s2_exp,
                threshold_value=self.min_expectancy,
                unit="USD/trade",
            )
        )

        # 2.4 Realized Profit Factor (GPF >= 1.25)
        s2_gpf = float(paper_metrics.get("profit_factor", 0.0))
        g2_4 = s2_gpf >= self.min_profit_factor
        if not g2_4:
            disqualifications.append(f"Stage 2 Realized Gross Profit Factor {s2_gpf:.2f} < {self.min_profit_factor}")
        gate_results.append(
            PromotionGateResult(
                name="Stage 2: Realized Profit Factor",
                stage=2,
                passed=g2_4,
                measured_value=s2_gpf,
                threshold_value=self.min_profit_factor,
                unit="ratio",
            )
        )

        # 2.5 Realized Max Drawdown (MaxDD <= 12.0%)
        s2_dd = float(paper_metrics.get("max_drawdown_pct", 100.0))
        g2_5 = s2_dd <= self.max_drawdown_pct
        if not g2_5:
            disqualifications.append(f"Stage 2 Realized Max Drawdown {s2_dd:.2f}% > {self.max_drawdown_pct}%")
        gate_results.append(
            PromotionGateResult(
                name="Stage 2: Realized Max Drawdown",
                stage=2,
                passed=g2_5,
                measured_value=s2_dd,
                threshold_value=self.max_drawdown_pct,
                unit="%",
            )
        )

        # 2.6 Parity Status (PARITY_PASS and Tracking error <= 2.0%)
        status_val = (
            parity_report.overall_status.value
            if hasattr(parity_report.overall_status, "value")
            else str(parity_report.overall_status)
        )
        g2_6 = (
            status_val == "PARITY_PASS"
            and parity_report.pnl_tracking_error_pct <= self.max_tracking_error_pct
        )
        if not g2_6:
            disqualifications.append(
                f"Stage 2 Parity Status {status_val} (tracking error {parity_report.pnl_tracking_error_pct:.2f}%) != PARITY_PASS"
            )
        gate_results.append(
            PromotionGateResult(
                name="Stage 2: Execution Parity Status",
                stage=2,
                passed=g2_6,
                measured_value=parity_report.pnl_tracking_error_pct,
                threshold_value=self.max_tracking_error_pct,
                unit="%",
                details=f"Parity: {status_val}",
            )
        )

        # 2.7 Risk Incidents (Exactly 0 Gate 1 DD or Gate 8 Kill Switch breaches)
        g2_7 = risk_incidents == 0
        if not g2_7:
            disqualifications.append(f"Stage 2 Risk Incidents count {risk_incidents} != 0")
        gate_results.append(
            PromotionGateResult(
                name="Stage 2: Risk Breach Incidents",
                stage=2,
                passed=g2_7,
                measured_value=float(risk_incidents),
                threshold_value=0.0,
                unit="incidents",
            )
        )

        stage2_passed = all([g2_1, g2_2, g2_3, g2_4, g2_5, g2_6, g2_7])
        eligible = stage1_passed and stage2_passed and len(disqualifications) == 0

        now_str = datetime.now(timezone.utc).isoformat()

        return PromotionEvaluationReport(
            strategy_id=strategy_id,
            stage1_passed=stage1_passed,
            stage2_passed=stage2_passed,
            eligible_for_live_candidate=eligible,
            evaluated_at=now_str,
            gate_results=gate_results,
            disqualifications=disqualifications,
        )

    def sign_off_promotion(
        self,
        report: PromotionEvaluationReport,
        operator_id: str,
        operator_secret: str,
        nonce: Optional[str] = None,
        is_ai_caller: bool = False,
    ) -> Dict[str, Any]:
        """Cryptographically sign off strategy promotion to LIVE_CANDIDATE.

        STRICT SAFETY ENFORCEMENT:
        - AI models possess ZERO authority to promote strategies.
        - Only authorized human operators can sign off.
        - Operator secret is validated against registered operator keys.
        - Strategy MUST be eligible (all Stage 1 and Stage 2 gates passed).
        - Generates HMAC signature with nonce and replay prevention.
        """
        # Strict AI Denial
        if is_ai_caller or operator_id.lower().startswith(("ai_", "bot_", "agent_", "llm_")):
            raise PermissionError(
                "SECURITY VETO: AI models possess ZERO authority to promote strategies, "
                "adjust risk limits, or initiate live candidacy."
            )

        if not operator_id or not operator_secret:
            raise ValueError("Human operator identification and secret key required.")

        # Validate operator credential against registered keys
        expected_secret = settings.OPERATOR_SIGNING_KEYS.get(operator_id)
        if not expected_secret or not hmac.compare_digest(operator_secret, expected_secret):
            raise PermissionError(f"Invalid operator credentials for '{operator_id}'. Sign-off rejected.")

        if not report.eligible_for_live_candidate:
            raise ValueError(
                f"Strategy {report.strategy_id} is NOT eligible for promotion. "
                f"Disqualifications: {report.disqualifications}"
            )

        import uuid
        now = datetime.now(timezone.utc).isoformat()
        use_nonce = nonce or uuid.uuid4().hex
        sign_payload = f"PROMOTION:{report.strategy_id}:{operator_id}:{now}:{use_nonce}"
        signature = hmac.new(
            expected_secret.encode("utf-8"),
            sign_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Verify signature immediately to enforce nonce consumption and validity
        is_valid = verify_promotion_signature(
            strategy_id=report.strategy_id,
            operator_id=operator_id,
            timestamp_iso=now,
            nonce=use_nonce,
            signature=signature,
            operator_secret=expected_secret,
        )
        if not is_valid:
            raise RuntimeError("Internal signature verification failed.")

        report.operator_sign_off_status = "SIGNED_OFF_BY_HUMAN_OPERATOR"
        report.signed_by = operator_id
        report.signature = signature
        report.signed_at = now

        return {
            "status": "PROMOTED_TO_LIVE_CANDIDATE",
            "strategy_id": report.strategy_id,
            "signed_by": operator_id,
            "signature": signature,
            "signed_at": now,
            "nonce": use_nonce,
            "new_state": "LIVE_CANDIDATE",
            "message": "Strategy verified through Phase 2 & Phase 3 gates and certified by authenticated human operator.",
        }

