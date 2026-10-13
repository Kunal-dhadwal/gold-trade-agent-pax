"""Twelve-Gate Deterministic Institutional Live Risk Engine for Phase 4.

Implements the formal risk architecture specified in Phase 4 Milestone 4.2:
- 12 deterministic mathematical risk gates with unconditional veto authority.
- Pre-flight bootstrap assertion: LIVE_TRADING_ENABLED == True required for live dispatch.
- Strict separation of controls: Entry controls vs Protective exit bypass rules.
- 1.0% dollar-risk per trade sizing clamping formula down to lot boundaries:
    Q_max_risk = (Equity * 0.010) / (|P_entry - P_SL| * ContractValue)
    Q_clamped = min(Q_requested, Q_max_risk) rounded down to integer contract lot boundaries.
- High-water mark integration via HighWaterMarkManager (Gate 1 Drawdown Cap >= 6.0%).
- Gate 12 Desynchronization & Burst Rejection Circuit Breaker (>= 3 rejections in 10m or drift != 0).
- Preserves frozen Phase 1-3 baseline (src/risk/paper_risk_engine.py) 100% untouched.

Protective Exit Bypass Rules:
  MAY BYPASS:
    - Gate 2 (Daily Max Loss Limit)
    - Gate 3 (Risk Per Trade & Sizing Clamping)
    - Gate 4 (Leverage & Notional Cap)
    - Gate 5 (Single Strategy Lock)
    - Gate 6 (Consecutive Loss Streak Guard)
    - Gate 7 (Market Regime Filter)
    - Gate 8 (Feed Latency: bypassed for emergency exits)
    - Gate 10 (Spread & Slippage Ceiling)
    - Gate 11 (Fee & Metadata Completeness)
  MUST ENFORCE (MANDATORY & UNBYPASSABLE):
    - Gate 1 (Portfolio Drawdown Cap >= 6.0% -> trips kill switch to FLATTENING)
    - Gate 9 (Server Clock Drift SLA > 1000ms)
    - Gate 12 (Desynchronization & Burst Rejection)
"""

from collections import deque
from datetime import date, datetime, timezone
import logging
import math
import statistics
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.config import settings
from src.database.connection import AsyncSessionLocal
from src.execution.delta.types import DeltaOrderRequest, DeltaProductSpecification
from src.risk.high_water_mark import HighWaterMarkManager
from src.risk.live_kill_switch import LiveKillSwitchFSM, LiveKillSwitchState

logger = logging.getLogger("gold_quant.risk.live_risk_engine")


class LiveRiskError(Exception):
    """Base exception for live risk engine failures."""
    pass


class LiveTradingDisabledError(LiveRiskError):
    """Raised when live order dispatch is attempted while LIVE_TRADING_ENABLED is False."""
    pass


class LiveRiskGateBreachError(LiveRiskError):
    """Raised when an unconditional risk gate is breached."""
    pass


class LiveRiskSizingError(LiveRiskError):
    """Raised when sizing inputs are invalid or fail closed."""
    pass


def assert_live_trading_enabled() -> None:
    """Pre-flight bootstrap assertion: LIVE_TRADING_ENABLED == True required for live dispatch.

    Fail-closed guard. Strictly prevents real-money or testnet order dispatch when disabled.
    """
    is_enabled = getattr(settings, "LIVE_TRADING_ENABLED", False)
    if not is_enabled:
        raise LiveTradingDisabledError(
            "FAIL-CLOSED BOOTSTRAP ASSERTION: LIVE_TRADING_ENABLED is False. "
            "Real-money order dispatch is strictly prohibited by configuration."
        )


class RiskEvaluationResult(dict):
    """Structured result of risk gate evaluation supporting attribute and dict access."""

    def __init__(
        self,
        approved: bool,
        failed_gate: Optional[Any] = None,
        message: str = "",
        clamped_size: Optional[int] = None,
        is_protective: bool = False,
        gate_1_drawdown_breach: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            approved=approved,
            failed_gate=failed_gate,
            message=message,
            clamped_size=clamped_size,
            is_protective=is_protective,
            gate_1_drawdown_breach=gate_1_drawdown_breach,
            details=details or {},
        )
        self.approved = approved
        self.failed_gate = failed_gate
        self.message = message
        self.clamped_size = clamped_size
        self.is_protective = is_protective
        self.gate_1_drawdown_breach = gate_1_drawdown_breach
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary representation."""
        return dict(self)


    def __repr__(self) -> str:
        return (
            f"RiskEvaluationResult(approved={self.approved}, failed_gate={self.failed_gate}, "
            f"message='{self.message}', clamped_size={self.clamped_size}, "
            f"gate_1_drawdown_breach={self.gate_1_drawdown_breach})"
        )


class LiveRiskEngine:
    """Institutional Twelve-Gate Deterministic Live Risk Engine."""

    def __init__(
        self,
        account_id: str,
        kill_switch: Optional[LiveKillSwitchFSM] = None,
        high_water_mark_manager: Optional[HighWaterMarkManager] = None,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        # Gate thresholds
        max_drawdown_pct: float = 6.0,  # Gate 1: 6.0% drawdown cap
        max_daily_loss_pct: float = 3.0,  # Gate 2: 3.0% daily loss limit
        max_risk_per_trade_pct: float = 1.0,  # Gate 3: 1.0% dollar risk per trade
        max_leverage: float = 5.0,  # Gate 4: 5.0x equity notional leverage ceiling
        max_notional_usd: float = 100000.0,  # Gate 4: absolute notional ceiling
        max_active_positions: int = 1,  # Gate 5: single strategy position lock
        max_consecutive_losses: int = 3,  # Gate 6: 3 consecutive loss limit
        loss_cooldown_seconds: float = 7200.0,  # Gate 6: 2-hour strategy cooldown
        allowed_regimes: Optional[Set[str]] = None,  # Gate 7: market regime whitelist
        max_feed_delay_seconds: float = 5.0,  # Gate 8: 5.0s feed delay ceiling
        max_clock_drift_ms: float = 1000.0,  # Gate 9: 1000ms clock drift SLA
        max_spread_usd: float = 1.50,  # Gate 10: $1.50 absolute spread ceiling
        max_spread_median_ratio: float = 2.0,  # Gate 10: 2.0x rolling 20 median spread ceiling
        fee_cache_ttl_seconds: float = 86400.0,  # Gate 11: 24h fee cache freshness
        burst_rejection_window_seconds: float = 600.0,  # Gate 12: 10-minute rejection window
        burst_rejection_threshold: int = 3,  # Gate 12: 3 rejections threshold
        product_spec: Optional[DeltaProductSpecification] = None,
    ):
        self.account_id = account_id
        self.kill_switch = kill_switch or LiveKillSwitchFSM()
        self.session_factory = session_factory or AsyncSessionLocal
        self.hwm_manager = high_water_mark_manager or HighWaterMarkManager(session_factory=self.session_factory)
        self.product_spec = product_spec

        # Thresholds
        self.max_drawdown_pct = max_drawdown_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_risk_per_trade_pct = max_risk_per_trade_pct
        self.max_leverage = max_leverage
        self.max_notional_usd = max_notional_usd
        self.max_active_positions = max_active_positions
        self.max_consecutive_losses = max_consecutive_losses
        self.loss_cooldown_seconds = loss_cooldown_seconds
        self.allowed_regimes = allowed_regimes or {"BULL_TREND", "BEAR_TREND", "RANGE", "NORMAL"}
        self.max_feed_delay_seconds = max_feed_delay_seconds
        self.max_clock_drift_ms = max_clock_drift_ms
        self.max_spread_usd = max_spread_usd
        self.max_spread_median_ratio = max_spread_median_ratio
        self.fee_cache_ttl_seconds = fee_cache_ttl_seconds
        self.burst_rejection_window_seconds = burst_rejection_window_seconds
        self.burst_rejection_threshold = burst_rejection_threshold

        # Runtime telemetry & state tracking
        self.current_equity: float = 0.0
        self.start_of_day_equity: float = 0.0
        self.current_utc_date: Optional[date] = None

        self.consecutive_losses: int = 0
        self.last_loss_timestamp: Optional[datetime] = None

        self.current_regime: str = "NORMAL"

        self.last_feed_timestamp: Optional[datetime] = None
        self.current_mark_price: float = 0.0

        self.last_clock_drift_ms: float = 0.0

        self.last_spread: float = 0.0
        self.spread_history: deque[float] = deque(maxlen=20)

        self.fee_schedule_verified: bool = False
        self.fee_schedule_timestamp: Optional[datetime] = None

        self.active_position_count: int = 0
        self.current_position_notional: float = 0.0

        self.quantity_drift: float = 0.0
        self.rejection_history: deque[datetime] = deque()

    async def initialize(
        self,
        current_equity: float,
        fencing_epoch: int = 1,
        session: Optional[AsyncSession] = None,
    ) -> float:
        """Initialize engine, load high-water mark, and initialize daily loss tracking.

        Args:
            current_equity: Initial account equity.
            fencing_epoch: Monotonic leadership epoch.
            session: Optional existing AsyncSession.

        Returns:
            The loaded or initialized peak equity.
        """
        if current_equity <= 0.0:
            raise ValueError(f"Initial equity must be positive: {current_equity}")

        self.current_equity = current_equity
        now = datetime.now(timezone.utc)
        self.current_utc_date = now.date()
        self.start_of_day_equity = current_equity

        peak = await self.hwm_manager.load_or_initialize(
            account_id=self.account_id,
            current_equity=current_equity,
            fencing_epoch=fencing_epoch,
            session=session,
        )
        logger.info(
            f"Initialized LiveRiskEngine for account '{self.account_id}': "
            f"current_equity={current_equity:.2f}, peak_equity={peak:.2f}, date={self.current_utc_date}"
        )
        return peak

    async def update_equity(
        self,
        current_equity: float,
        fencing_epoch: int = 1,
        session: Optional[AsyncSession] = None,
    ) -> Tuple[float, bool]:
        """Update current equity and persist new peak in high-water mark manager if exceeded."""
        if current_equity <= 0.0:
            raise ValueError(f"Current equity must be positive: {current_equity}")

        self.current_equity = current_equity
        now = datetime.now(timezone.utc)
        self._check_and_rollover_daily_equity(current_equity, now)

        return await self.hwm_manager.update_peak(
            account_id=self.account_id,
            current_equity=current_equity,
            fencing_epoch=fencing_epoch,
            session=session,
        )

    def calculate_drawdown_pct(self, current_equity: Optional[float] = None) -> float:
        """Calculate peak-to-trough portfolio drawdown percentage from persisted peak."""
        eq = current_equity if current_equity is not None else self.current_equity
        return self.hwm_manager.calculate_drawdown_pct(self.account_id, eq)

    def calculate_daily_loss_pct(self, current_equity: Optional[float] = None) -> float:
        """Calculate daily percentage loss from 00:00 UTC start-of-day equity."""
        eq = current_equity if current_equity is not None else self.current_equity
        if self.start_of_day_equity <= 0.0:
            return 0.0
        if eq >= self.start_of_day_equity:
            return 0.0
        loss = ((self.start_of_day_equity - eq) / self.start_of_day_equity) * 100.0
        return max(0.0, round(loss, 4))

    def _check_and_rollover_daily_equity(self, current_equity: float, now: datetime) -> None:
        """Check for UTC midnight rollover and reset start-of-day equity baseline."""
        today = now.date()
        if self.current_utc_date is None:
            self.current_utc_date = today
            self.start_of_day_equity = current_equity
        elif self.current_utc_date != today:
            logger.info(
                f"Gate 2 Daily Loss Rollover from {self.current_utc_date} to {today}: "
                f"resetting start_of_day_equity to {current_equity:.2f}"
            )
            self.current_utc_date = today
            self.start_of_day_equity = current_equity

    def record_trade_result(self, realized_pnl: float, timestamp: Optional[datetime] = None) -> None:
        """Record trade execution outcome for Gate 6 consecutive loss streak tracking."""
        now = timestamp or datetime.now(timezone.utc)
        if realized_pnl < 0.0:
            self.consecutive_losses += 1
            self.last_loss_timestamp = now
            logger.warning(
                f"Gate 6 Loss Streak: consecutive_losses={self.consecutive_losses} "
                f"(realized_pnl={realized_pnl:.2f})"
            )
        else:
            if self.consecutive_losses > 0:
                logger.info(f"Gate 6 Loss Streak broken: reset from {self.consecutive_losses} to 0")
            self.consecutive_losses = 0

    def record_rejection(self, reason: str, timestamp: Optional[datetime] = None) -> bool:
        """Record an order rejection for Gate 12 burst rejection circuit breaker.

        Returns True if burst rejection circuit breaker tripped, False otherwise.
        """
        now = timestamp or datetime.now(timezone.utc)
        self.rejection_history.append(now)
        self._purge_stale_rejections(now)

        if len(self.rejection_history) >= self.burst_rejection_threshold:
            logger.critical(
                f"GATE 12 BURST REJECTION TRIPPED: {len(self.rejection_history)} rejections in "
                f"{self.burst_rejection_window_seconds}s! Tripping Live Kill Switch."
            )
            self.kill_switch.trigger(
                reason=f"GATE_12_BURST_REJECTION: {len(self.rejection_history)} rejections in 10m ({reason})",
                triggered_by="LIVE_RISK_ENGINE",
                policy="BURST_REJECTION",
                details={"rejection_count": len(self.rejection_history), "reason": reason},
            )
            return True
        return False

    def _purge_stale_rejections(self, now: datetime) -> None:
        """Purge rejections outside rolling burst rejection window."""
        cutoff_sec = self.burst_rejection_window_seconds
        while self.rejection_history:
            oldest = self.rejection_history[0]
            if (now - oldest).total_seconds() > cutoff_sec:
                self.rejection_history.popleft()
            else:
                break

    def set_quantity_drift(self, drift: float) -> bool:
        """Set quantity drift for Gate 12 desynchronization monitoring.

        Returns True if quantity drift tripped kill switch, False otherwise.
        """
        self.quantity_drift = drift
        if abs(drift) > 1e-6:
            logger.critical(
                f"GATE 12 DESYNCHRONIZATION TRIPPED: Quantity drift {drift} != 0! "
                "Tripping Live Kill Switch into MANUAL_RESET_REQUIRED (zero auto-liquidation)."
            )
            self.kill_switch.trigger(
                reason=f"GATE_12_QUANTITY_DRIFT: Drift {drift} detected",
                triggered_by="LIVE_RISK_ENGINE",
                policy="GHOST_EXPOSURE",
                details={"quantity_drift": drift, "automated_liquidation": False},
            )
            return True
        return False

    def update_spread(self, spread: float) -> None:
        """Update current bid-ask spread and record in rolling 20-period history for Gate 10."""
        self.last_spread = spread
        self.spread_history.append(spread)

    def update_market_data(self, mark_price: float, timestamp: Optional[datetime] = None) -> None:
        """Update market data feed timestamp and mark price for Gate 8."""
        self.current_mark_price = mark_price
        self.last_feed_timestamp = timestamp or datetime.now(timezone.utc)

    def update_clock_drift(self, drift_ms: float) -> None:
        """Update clock drift metric for Gate 9."""
        self.last_clock_drift_ms = drift_ms

    def set_fee_schedule_verified(self, verified: bool, timestamp: Optional[datetime] = None) -> None:
        """Update fee schedule verification status for Gate 11."""
        self.fee_schedule_verified = verified
        self.fee_schedule_timestamp = timestamp or (datetime.now(timezone.utc) if verified else None)

    def clamp_order_size_for_risk(
        self,
        order_request: DeltaOrderRequest,
        equity: float,
        product_spec: Optional[DeltaProductSpecification] = None,
    ) -> Tuple[Optional[int], Optional[str]]:
        """Compute Gate 3 dollar-risk clamping down to integer contract lot boundaries.

        Formula:
            MaxDollarRisk = Equity * 0.010
            StopDistance = |P_entry - P_SL|
            Q_max_risk = MaxDollarRisk / (StopDistance * ContractValue)
            Q_clamped = min(Q_requested, Q_max_risk) rounded down to integer lot boundaries.

        Fail-Closed Safety Handling:
            - Missing stop loss price: reject.
            - Stop distance <= 0: reject.
            - Contract value <= 0: reject.
            - Equity <= 0: reject.
            - Clamped quantity < min_order_size: reject.

        Returns:
            Tuple[Optional[int], Optional[str]]: (clamped_size, error_reason)
        """
        if equity <= 0.0:
            return None, f"Account equity {equity} is non-positive"

        spec = product_spec or self.product_spec
        contract_value = getattr(spec, "contract_value", 0.001) if spec else 0.001
        lot_size = getattr(spec, "min_order_size", 1) if spec else 1
        min_order_size = getattr(spec, "min_order_size", 1) if spec else 1

        if contract_value <= 0.0:
            return None, f"Invalid contract value {contract_value} <= 0"

        entry_price = order_request.limit_price or self.current_mark_price
        if entry_price is None or entry_price <= 0.0:
            return None, "Entry price unavailable or non-positive"

        stop_loss_price = order_request.stop_loss_price
        if stop_loss_price is None:
            return None, "Missing stop loss price for risk sizing calculation"

        stop_distance = abs(entry_price - stop_loss_price)
        if stop_distance <= 0.0 or math.isclose(stop_distance, 0.0):
            return None, f"Invalid zero stop distance (|{entry_price} - {stop_loss_price}|)"

        # Max dollar risk is 1.0% of equity
        max_dollar_risk = equity * (self.max_risk_per_trade_pct / 100.0)
        q_max_risk = max_dollar_risk / (stop_distance * contract_value)

        # Clamp requested size
        q_requested = float(order_request.size)
        q_clamped = min(q_requested, q_max_risk)

        # Round down to integer lot boundaries
        lot_step = max(1, int(lot_size))
        clamped_lots = int(math.floor(q_clamped / lot_step)) * lot_step

        if clamped_lots < min_order_size:
            return None, (
                f"Clamped size {clamped_lots} is below minimum order size {min_order_size} "
                f"(requested={order_request.size}, max_risk_lots={q_max_risk:.2f})"
            )

        return clamped_lots, None

    def check_and_clamp_leverage(
        self,
        order_request: DeltaOrderRequest,
        equity: float,
        product_spec: Optional[DeltaProductSpecification] = None,
    ) -> Tuple[Optional[int], Optional[str]]:
        """Compute Gate 4 leverage and notional ceiling clamping.

        Formula:
            OrderNotional = Q * EntryPrice * ContractValue
            TotalNotional = CurrentNotional + OrderNotional <= min(Equity * 5.0, MaxNotional)

        Returns:
            Tuple[Optional[int], Optional[str]]: (clamped_size, error_reason)
        """
        if equity <= 0.0:
            return None, f"Account equity {equity} is non-positive"

        spec = product_spec or self.product_spec
        contract_value = getattr(spec, "contract_value", 0.001) if spec else 0.001
        lot_size = getattr(spec, "min_order_size", 1) if spec else 1
        min_order_size = getattr(spec, "min_order_size", 1) if spec else 1

        entry_price = order_request.limit_price or self.current_mark_price
        if entry_price is None or entry_price <= 0.0:
            return None, "Entry price unavailable or non-positive for leverage calculation"

        max_allowed_notional = min(equity * self.max_leverage, self.max_notional_usd)
        available_notional = max(0.0, max_allowed_notional - self.current_position_notional)

        order_notional = float(order_request.size) * entry_price * contract_value
        if order_notional <= available_notional:
            return order_request.size, None

        # Clamp quantity to available notional
        q_max_notional = available_notional / (entry_price * contract_value)
        lot_step = max(1, int(lot_size))
        clamped_lots = int(math.floor(q_max_notional / lot_step)) * lot_step

        if clamped_lots < min_order_size:
            return None, (
                f"Order notional ${order_notional:.2f} exceeds available notional ${available_notional:.2f} "
                f"(max_leverage={self.max_leverage}x, clamped={clamped_lots} < min={min_order_size})"
            )

        return clamped_lots, None

    def evaluate_order(self, context: Dict[str, Any]) -> RiskEvaluationResult:
        """Evaluate an order request against the twelve deterministic live risk gates.

        Context dictionary fields:
            - order_request: DeltaOrderRequest instance
            - is_protective: bool (True for stop loss, take profit, reduce_only exit, emergency flatten)
            - client_order_id: Optional[str]
            - current_equity: Optional[float] (overrides cached equity)
            - current_mark_price: Optional[float]
            - clock_drift_ms: Optional[float]
            - feed_timestamp: Optional[datetime]
            - current_spread: Optional[float]
            - regime: Optional[str]
            - product_spec: Optional[DeltaProductSpecification]
            - active_position_count: Optional[int]
            - enforce_bootstrap_assertion: Optional[bool] (default False; True in live dispatch)

        Returns:
            RiskEvaluationResult with approved flag, failed gate, message, and clamped size.
        """
        now = datetime.now(timezone.utc)
        order_request: DeltaOrderRequest = context.get("order_request")
        if order_request is None:
            return RiskEvaluationResult(
                approved=False,
                failed_gate="VALIDATION",
                message="Order evaluation failed: missing order_request",
            )

        # Treat reduce_only orders as protective exits
        is_protective: bool = bool(context.get("is_protective", False) or getattr(order_request, "reduce_only", False))

        # Check pre-flight bootstrap assertion if requested
        if context.get("enforce_bootstrap_assertion", False):
            try:
                assert_live_trading_enabled()
            except LiveTradingDisabledError as exc:
                return RiskEvaluationResult(
                    approved=False,
                    failed_gate="PRE_FLIGHT_BOOTSTRAP",
                    message=str(exc),
                    is_protective=is_protective,
                )

        # Equity evaluation
        equity = float(context.get("current_equity") if context.get("current_equity") is not None else self.current_equity)
        if equity > 0.0:
            self._check_and_rollover_daily_equity(equity, now)

        # Kill Switch Check
        if self.kill_switch.is_active():
            if not is_protective:
                return RiskEvaluationResult(
                    approved=False,
                    failed_gate="KILL_SWITCH",
                    message=f"Live Kill Switch is active in state {self.kill_switch.state.value}; new trading blocked",
                    is_protective=is_protective,
                )
            if self.kill_switch.state == LiveKillSwitchState.FLATTENED:
                return RiskEvaluationResult(
                    approved=False,
                    failed_gate="KILL_SWITCH",
                    message="Live Kill Switch is FLATTENED; all positions confirmed flat",
                    is_protective=is_protective,
                )

        # ----------------------------------------------------------------------
        # GATE 1: Portfolio Peak-to-Trough Drawdown Cap (>= 6.0%)
        # MANDATORY & UNBYPASSABLE: Evaluated for ALL orders.
        # ----------------------------------------------------------------------
        drawdown_pct = self.calculate_drawdown_pct(equity)
        gate_1_breached = drawdown_pct >= self.max_drawdown_pct
        if gate_1_breached:
            self.kill_switch.trigger_drawdown_flatten(drawdown_pct=drawdown_pct)
            if not is_protective:
                return RiskEvaluationResult(
                    approved=False,
                    failed_gate=1,
                    message=(
                        f"Gate 1 Peak-to-Trough Drawdown Breach: Drawdown {drawdown_pct:.2f}% >= "
                        f"{self.max_drawdown_pct}%. Kill Switch FLATTENING latched."
                    ),
                    is_protective=is_protective,
                    gate_1_drawdown_breach=True,
                )

        # ----------------------------------------------------------------------
        # PROTECTIVE EXIT BYPASS EVALUATION
        # Protective exits bypass Gates 2, 3, 4, 5, 6, 7, 8, 10, 11.
        # Protective exits MUST enforce Gates 1, 9, 12.
        # ----------------------------------------------------------------------
        if is_protective:
            # Gate 9 SLA Check for Protective Exits
            drift_ms = float(
                context.get("clock_drift_ms") if context.get("clock_drift_ms") is not None else self.last_clock_drift_ms
            )
            if abs(drift_ms) > self.max_clock_drift_ms:
                return RiskEvaluationResult(
                    approved=False,
                    failed_gate=9,
                    message=(
                        f"Gate 9 Clock Drift SLA Breach on Protective Exit: Drift {abs(drift_ms):.1f}ms > "
                        f"{self.max_clock_drift_ms}ms"
                    ),
                    is_protective=True,
                    gate_1_drawdown_breach=gate_1_breached,
                )

            # Gate 12 Desynchronization Check for Protective Exits
            if abs(self.quantity_drift) > 1e-6:
                return RiskEvaluationResult(
                    approved=False,
                    failed_gate=12,
                    message=f"Gate 12 Quantity Drift Breach on Protective Exit: Drift {self.quantity_drift} != 0",
                    is_protective=True,
                    gate_1_drawdown_breach=gate_1_breached,
                )

            # Approved protective exit
            return RiskEvaluationResult(
                approved=True,
                clamped_size=order_request.size,
                is_protective=True,
                gate_1_drawdown_breach=gate_1_breached,
            )

        # ----------------------------------------------------------------------
        # ENTRY ORDER GATES (Gates 2 through 12)
        # ----------------------------------------------------------------------

        # GATE 2: Daily Max Loss Limit (>= 3.0% from 00:00 UTC equity)
        daily_loss_pct = self.calculate_daily_loss_pct(equity)
        if daily_loss_pct >= self.max_daily_loss_pct:
            return RiskEvaluationResult(
                approved=False,
                failed_gate=2,
                message=(
                    f"Gate 2 Daily Max Loss Breach: Loss {daily_loss_pct:.2f}% >= "
                    f"{self.max_daily_loss_pct}%. New entries blocked for remainder of UTC day."
                ),
                is_protective=False,
            )

        # GATE 3: Risk Per Trade & Sizing Clamping (1.0% equity)
        spec = context.get("product_spec") or self.product_spec
        clamped_size, sizing_error = self.clamp_order_size_for_risk(order_request, equity, spec)
        if clamped_size is None:
            return RiskEvaluationResult(
                approved=False,
                failed_gate=3,
                message=f"Gate 3 Risk Sizing Rejection: {sizing_error}",
                is_protective=False,
            )
        if clamped_size < order_request.size:
            logger.info(
                f"Gate 3 Sizing Clamped: Requested {order_request.size} lots clamped to {clamped_size} lots "
                f"to satisfy 1.0% dollar risk cap"
            )
            order_request.size = clamped_size

        # GATE 4: Leverage & Notional Cap (5.0x equity)
        lev_clamped_size, lev_error = self.check_and_clamp_leverage(order_request, equity, spec)
        if lev_clamped_size is None:
            return RiskEvaluationResult(
                approved=False,
                failed_gate=4,
                message=f"Gate 4 Leverage Breach: {lev_error}",
                is_protective=False,
            )
        if lev_clamped_size < order_request.size:
            logger.info(
                f"Gate 4 Leverage Clamped: Requested {order_request.size} lots clamped to {lev_clamped_size} lots "
                f"to satisfy {self.max_leverage}x equity cap"
            )
            order_request.size = lev_clamped_size

        # GATE 5: Single Strategy Lock (Active positions >= 1)
        active_pos = int(
            context.get("active_position_count")
            if context.get("active_position_count") is not None
            else self.active_position_count
        )
        if active_pos >= self.max_active_positions:
            return RiskEvaluationResult(
                approved=False,
                failed_gate=5,
                message=(
                    f"Gate 5 Single Strategy Lock Rejection: Account already has {active_pos} active position(s). "
                    f"Max permitted is {self.max_active_positions}."
                ),
                is_protective=False,
            )

        # GATE 6: Consecutive Loss Streak Guard (>= 3 trades -> 2h cooldown)
        if self.consecutive_losses >= self.max_consecutive_losses:
            if self.last_loss_timestamp is not None:
                loss_time = self.last_loss_timestamp.replace(tzinfo=timezone.utc) if self.last_loss_timestamp.tzinfo is None else self.last_loss_timestamp
                cooldown_elapsed = (now - loss_time).total_seconds()
                if cooldown_elapsed < self.loss_cooldown_seconds:
                    rem_sec = self.loss_cooldown_seconds - cooldown_elapsed
                    return RiskEvaluationResult(
                        approved=False,
                        failed_gate=6,
                        message=(
                            f"Gate 6 Loss Streak Cooldown Rejection: {self.consecutive_losses} consecutive losses; "
                            f"strategy cooldown active ({rem_sec:.0f}s remaining of {self.loss_cooldown_seconds:.0f}s)."
                        ),
                        is_protective=False,
                    )
                else:
                    # Cooldown elapsed: reset loss streak
                    logger.info(f"Gate 6 Strategy cooldown elapsed after {cooldown_elapsed:.0f}s; resetting loss streak.")
                    self.consecutive_losses = 0

        # GATE 7: Market Regime Filter
        current_regime = str(context.get("regime") or self.current_regime).upper()
        if current_regime not in self.allowed_regimes:
            return RiskEvaluationResult(
                approved=False,
                failed_gate=7,
                message=(
                    f"Gate 7 Market Regime Filter Rejection: Detected regime '{current_regime}' is not "
                    f"in authorized strategy whitelist {sorted(list(self.allowed_regimes))}."
                ),
                is_protective=False,
            )

        # GATE 8: Feed Latency & Stale Bar (> 5.0s)
        feed_ts = context.get("feed_timestamp") or self.last_feed_timestamp
        if feed_ts is None:
            return RiskEvaluationResult(
                approved=False,
                failed_gate=8,
                message="Gate 8 Stale Bar Rejection: Market data feed timestamp is unavailable (fail-closed).",
                is_protective=False,
            )
        feed_time = feed_ts.replace(tzinfo=timezone.utc) if feed_ts.tzinfo is None else feed_ts
        feed_delay = (now - feed_time).total_seconds()
        if feed_delay > self.max_feed_delay_seconds:
            return RiskEvaluationResult(
                approved=False,
                failed_gate=8,
                message=(
                    f"Gate 8 Stale Bar Rejection: Feed latency {feed_delay:.2f}s exceeds "
                    f"{self.max_feed_delay_seconds:.1f}s SLA threshold."
                ),
                is_protective=False,
            )

        # GATE 9: Server Clock Drift SLA (> 1000ms)
        drift_ms = float(
            context.get("clock_drift_ms") if context.get("clock_drift_ms") is not None else self.last_clock_drift_ms
        )
        if abs(drift_ms) > self.max_clock_drift_ms:
            return RiskEvaluationResult(
                approved=False,
                failed_gate=9,
                message=(
                    f"Gate 9 Clock Drift SLA Rejection: Server clock drift {abs(drift_ms):.1f}ms exceeds "
                    f"{self.max_clock_drift_ms:.0f}ms SLA limit."
                ),
                is_protective=False,
            )

        # GATE 10: Spread & Slippage Ceiling (> $1.50 or > 2.0x median)
        spread = float(
            context.get("current_spread") if context.get("current_spread") is not None else self.last_spread
        )
        if spread > self.max_spread_usd:
            return RiskEvaluationResult(
                approved=False,
                failed_gate=10,
                message=(
                    f"Gate 10 Spread Ceiling Rejection: Current spread ${spread:.2f} exceeds "
                    f"${self.max_spread_usd:.2f} maximum ceiling."
                ),
                is_protective=False,
            )
        if len(self.spread_history) >= 20:
            med_spread = statistics.median(self.spread_history)
            if med_spread > 0.0 and spread > (self.max_spread_median_ratio * med_spread):
                return RiskEvaluationResult(
                    approved=False,
                    failed_gate=10,
                    message=(
                        f"Gate 10 Spread Ratio Rejection: Spread ${spread:.2f} exceeds "
                        f"{self.max_spread_median_ratio}x rolling 20 median (${med_spread:.2f})."
                    ),
                    is_protective=False,
                )

        # GATE 11: Fee & Metadata Completeness (Tier 1/2 fees verified and < 24h)
        if not self.fee_schedule_verified:
            return RiskEvaluationResult(
                approved=False,
                failed_gate=11,
                message="Gate 11 Fee Metadata Rejection: Fee schedule has not been verified (fail-closed).",
                is_protective=False,
            )
        if self.fee_schedule_timestamp is None:
            return RiskEvaluationResult(
                approved=False,
                failed_gate=11,
                message="Gate 11 Fee Metadata Rejection: Fee schedule timestamp missing (fail-closed).",
                is_protective=False,
            )
        fee_time = self.fee_schedule_timestamp.replace(tzinfo=timezone.utc) if self.fee_schedule_timestamp.tzinfo is None else self.fee_schedule_timestamp
        fee_age = (now - fee_time).total_seconds()
        if fee_age > self.fee_cache_ttl_seconds:
            return RiskEvaluationResult(
                approved=False,
                failed_gate=11,
                message=(
                    f"Gate 11 Fee Metadata Rejection: Fee schedule cache is stale ({fee_age:.0f}s > "
                    f"{self.fee_cache_ttl_seconds:.0f}s TTL)."
                ),
                is_protective=False,
            )

        # GATE 12: Desynchronization & Burst Rejection Circuit Breaker
        if abs(self.quantity_drift) > 1e-6:
            self.kill_switch.trigger(
                reason=f"GATE_12_QUANTITY_DRIFT: Drift {self.quantity_drift} != 0",
                triggered_by="LIVE_RISK_ENGINE",
                policy="GHOST_EXPOSURE",
            )
            return RiskEvaluationResult(
                approved=False,
                failed_gate=12,
                message=f"Gate 12 Desynchronization Rejection: Quantity drift {self.quantity_drift} != 0.",
                is_protective=False,
            )

        self._purge_stale_rejections(now)
        if len(self.rejection_history) >= self.burst_rejection_threshold:
            self.kill_switch.trigger(
                reason=f"GATE_12_BURST_REJECTION: {len(self.rejection_history)} rejections in 10m",
                triggered_by="LIVE_RISK_ENGINE",
                policy="BURST_REJECTION",
            )
            return RiskEvaluationResult(
                approved=False,
                failed_gate=12,
                message=(
                    f"Gate 12 Burst Rejection Rejection: {len(self.rejection_history)} order rejections in "
                    f"{self.burst_rejection_window_seconds}s window."
                ),
                is_protective=False,
            )

        # All 12 Gates Passed
        return RiskEvaluationResult(
            approved=True,
            clamped_size=order_request.size,
            is_protective=False,
            gate_1_drawdown_breach=False,
        )

    def evaluate_for_dispatch(self, context: Dict[str, Any]) -> RiskEvaluationResult:
        """Evaluate order for live dispatch with mandatory pre-flight bootstrap assertion enforced."""
        ctx = dict(context)
        ctx["enforce_bootstrap_assertion"] = True
        return self.evaluate_order(ctx)
