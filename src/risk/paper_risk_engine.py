"""Deterministic Paper Risk Engine enforcing 8 institutional mathematical risk gates."""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from src.core.config import settings
from src.paper.specifications import InstrumentSpecification, get_instrument_specification
from src.risk.kill_switch import KillSwitchFSM

logger = logging.getLogger(__name__)


class RiskDecision:
    """Immutable evaluation result of the Deterministic Risk Engine."""

    def __init__(
        self,
        approved: bool,
        gate_failed: Optional[str] = None,
        reason: Optional[str] = None,
        adjusted_quantity: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.approved = approved
        self.gate_failed = gate_failed
        self.reason = reason
        self.adjusted_quantity = adjusted_quantity
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": self.approved,
            "gate_failed": self.gate_failed,
            "reason": self.reason,
            "adjusted_quantity": self.adjusted_quantity,
            "metadata": self.metadata,
        }


class PaperRiskEngine:
    """Evaluates 8 deterministic mathematical risk gates with unconditional veto authority."""

    def __init__(
        self,
        max_drawdown_pct: float = 10.0,
        max_daily_loss_pct: float = 3.0,
        max_risk_per_trade_pct: float = 1.0,
        max_consecutive_losses: int = 5,
        max_spread_ratio: float = 2.0,
        max_spread_usd: float = 1.50,
        kill_switch: Optional[KillSwitchFSM] = None,
    ):
        self.max_drawdown_pct = max_drawdown_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.max_spread_ratio = max_spread_ratio
        self.max_spread_usd = max_spread_usd
        self.max_concurrent_positions = 1
        self.kill_switch = kill_switch or KillSwitchFSM()


        # Rolling spread window (last 20 completed 5m bars)
        self._spread_history: List[float] = []

    def update_spread_history(self, spread: float) -> None:
        """Add latest bar spread to rolling 20-bar window."""
        if spread > 0:
            self._spread_history.append(spread)
            if len(self._spread_history) > 20:
                self._spread_history.pop(0)

    def evaluate_order(
        self,
        account_equity: float,
        account_cash: float,
        starting_daily_equity: float,
        peak_equity: float,
        symbol: str,
        direction: str,
        requested_quantity: float,
        entry_price: float,
        stop_loss: float,
        current_spread: float,
        strategy_consecutive_losses: int = 0,
        strategy_allowed_regimes: Optional[List[str]] = None,
        current_market_regime: str = "TRENDING_BULL",
    ) -> RiskDecision:
        """Evaluate incoming trade signal against all 8 gates in strict hierarchical order."""
        spec: InstrumentSpecification = get_instrument_specification(symbol)

        # -------------------------------------------------------------
        # Gate 8: Emergency Kill Switch
        # -------------------------------------------------------------
        if self.kill_switch.is_active():
            return RiskDecision(
                approved=False,
                gate_failed="GATE_8_KILL_SWITCH",
                reason=f"Emergency Kill Switch is active ({self.kill_switch.trigger_reason}). All trading blocked.",
            )

        # -------------------------------------------------------------
        # Gate 1: Portfolio Drawdown Circuit Breaker
        # DrawdownPct = (PeakEquity - Equity) / PeakEquity * 100
        # -------------------------------------------------------------
        if peak_equity > 0:
            current_drawdown_pct = ((peak_equity - account_equity) / peak_equity) * 100.0
            if current_drawdown_pct >= self.max_drawdown_pct:
                # Trigger Kill Switch on hard drawdown breach
                self.kill_switch.trigger(
                    reason=f"Gate 1 breached: Drawdown {current_drawdown_pct:.2f}% >= {self.max_drawdown_pct:.2f}% cap",
                    triggered_by="RISK_ENGINE_GATE_1",
                )
                return RiskDecision(
                    approved=False,
                    gate_failed="GATE_1_MAX_DRAWDOWN",
                    reason=f"Portfolio drawdown {current_drawdown_pct:.2f}% exceeds limit {self.max_drawdown_pct:.2f}%.",
                )

        # -------------------------------------------------------------
        # Gate 2: Daily Loss Limit
        # DailyLossPct = (StartingDailyEquity - Equity) / StartingDailyEquity * 100
        # -------------------------------------------------------------
        if starting_daily_equity > 0:
            daily_loss_pct = ((starting_daily_equity - account_equity) / starting_daily_equity) * 100.0
            if daily_loss_pct >= self.max_daily_loss_pct:
                return RiskDecision(
                    approved=False,
                    gate_failed="GATE_2_DAILY_LOSS",
                    reason=f"Daily loss {daily_loss_pct:.2f}% exceeds limit {self.max_daily_loss_pct:.2f}%. Trading paused for today.",
                )

        # -------------------------------------------------------------
        # Gate 6: Consecutive Loss Streak Guard
        # -------------------------------------------------------------
        if strategy_consecutive_losses >= self.max_consecutive_losses:
            return RiskDecision(
                approved=False,
                gate_failed="GATE_6_LOSS_STREAK",
                reason=f"Consecutive loss streak ({strategy_consecutive_losses}) reached limit {self.max_consecutive_losses}.",
            )

        # -------------------------------------------------------------
        # Gate 5: Unambiguous Spread Spike Guard
        # SpreadRatio = CurrentSpread / MedianSpread_20
        # -------------------------------------------------------------
        if current_spread > self.max_spread_usd:
            return RiskDecision(
                approved=False,
                gate_failed="GATE_5_SPREAD_SPIKE",
                reason=f"Current spread ${current_spread:.2f} exceeds absolute cap ${self.max_spread_usd:.2f}.",
            )
        if len(self._spread_history) >= 5:
            median_spread = float(np.median(self._spread_history))
            if median_spread > 0:
                spread_ratio = current_spread / median_spread
                if spread_ratio > self.max_spread_ratio:
                    return RiskDecision(
                        approved=False,
                        gate_failed="GATE_5_SPREAD_SPIKE",
                        reason=f"Spread ratio {spread_ratio:.2f}x exceeds median cap {self.max_spread_ratio:.2f}x (median={median_spread:.2f}).",
                    )

        # -------------------------------------------------------------
        # Gate 7: Market Regime Alignment
        # -------------------------------------------------------------
        if strategy_allowed_regimes and len(strategy_allowed_regimes) > 0:
            if current_market_regime not in strategy_allowed_regimes:
                return RiskDecision(
                    approved=False,
                    gate_failed="GATE_7_REGIME_FILTER",
                    reason=f"Current regime '{current_market_regime}' is not permitted by strategy whitelisted regimes: {strategy_allowed_regimes}.",
                )

        # -------------------------------------------------------------
        # Gate 3: Max Risk Per Trade & Sizing Clamping
        # DollarRisk = |Entry - SL| * Quantity * ContractSize <= 1.0% Equity
        # -------------------------------------------------------------
        price_risk = abs(entry_price - stop_loss)
        if price_risk <= 0:
            return RiskDecision(
                approved=False,
                gate_failed="GATE_3_INVALID_STOP_LOSS",
                reason="Invalid stop loss: distance to entry price is zero.",
            )

        max_allowed_dollar_risk = account_equity * (self.max_risk_per_trade_pct / 100.0)
        # Quantity that satisfies max allowed risk
        max_risk_quantity = max_allowed_dollar_risk / (price_risk * spec.contract_size)
        clamped_quantity = min(requested_quantity, max_risk_quantity)
        clamped_quantity = spec.round_quantity(clamped_quantity)

        if clamped_quantity < spec.minimum_quantity:
            return RiskDecision(
                approved=False,
                gate_failed="GATE_3_RISK_PER_TRADE",
                reason=f"Risk-adjusted quantity {clamped_quantity} is below minimum allowable quantity {spec.minimum_quantity}.",
            )

        # -------------------------------------------------------------
        # Gate 4: Position Size & Portfolio Leverage Cap
        # Notional <= 5.0 * Equity and Quantity <= MaximumQuantity
        # -------------------------------------------------------------
        if clamped_quantity > spec.maximum_quantity:
            clamped_quantity = spec.maximum_quantity

        notional = spec.calculate_notional(entry_price, clamped_quantity)
        max_notional_cap = account_equity * 5.0  # 5x leverage cap
        if notional > max_notional_cap:
            clamped_quantity = spec.round_quantity(max_notional_cap / (entry_price * spec.contract_size))
            if clamped_quantity < spec.minimum_quantity:
                return RiskDecision(
                    approved=False,
                    gate_failed="GATE_4_LEVERAGE_CAP",
                    reason="Position notional exceeds 5x portfolio equity leverage limit.",
                )

        # All gates passed successfully
        return RiskDecision(
            approved=True,
            adjusted_quantity=clamped_quantity,
            metadata={
                "original_quantity": requested_quantity,
                "adjusted_quantity": clamped_quantity,
                "notional_usd": spec.calculate_notional(entry_price, clamped_quantity),
                "dollar_risk": price_risk * clamped_quantity * spec.contract_size,
            },
        )
