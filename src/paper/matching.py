"""Virtual Order Matching Engine and Order State Machine for Phase 3."""

from datetime import datetime, timezone
from enum import Enum
import logging
from typing import Dict, Optional, Set, Tuple
import uuid
from src.paper.specifications import InstrumentSpecification

logger = logging.getLogger(__name__)


class OrderState(str, Enum):
    """Deterministic finite state machine states for paper orders."""

    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


_LEGAL_TRANSITIONS: Dict[OrderState, Set[OrderState]] = {
    OrderState.CREATED: {OrderState.SUBMITTED, OrderState.REJECTED},
    OrderState.SUBMITTED: {OrderState.ACCEPTED, OrderState.REJECTED, OrderState.FAILED},
    OrderState.ACCEPTED: {OrderState.FILLED, OrderState.PARTIALLY_FILLED, OrderState.CANCELLED, OrderState.EXPIRED},
    OrderState.PARTIALLY_FILLED: {OrderState.FILLED, OrderState.CANCELLED, OrderState.EXPIRED},
    OrderState.FILLED: set(),
    OrderState.REJECTED: set(),
    OrderState.CANCELLED: set(),
    OrderState.EXPIRED: set(),
    OrderState.FAILED: set(),
}


class PaperOrder:
    """Represents a virtual paper order governed by strict FSM transitions."""

    def __init__(
        self,
        account_id: str,
        symbol: str,
        direction: str,
        order_type: str,
        quantity: float,
        requested_price: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        order_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ):
        self.order_id = order_id or str(uuid.uuid4())
        self.account_id = account_id
        self.symbol = symbol.strip().upper()
        self.direction = direction.strip().upper()
        self.order_type = order_type.strip().upper()
        self.quantity = float(quantity)
        self.requested_price = float(requested_price)
        self.stop_loss = float(stop_loss) if stop_loss is not None else 0.0
        self.take_profit = float(take_profit) if take_profit is not None else 0.0
        self.idempotency_key = idempotency_key or f"{self.order_id}:{datetime.now(timezone.utc).isoformat()}"

        self.status = OrderState.CREATED
        self.execution_price: Optional[float] = None
        self.fee: float = 0.0
        self.slippage: float = 0.0
        self.rejection_reason: Optional[str] = None
        self.created_at = datetime.now(timezone.utc)
        self.filled_at: Optional[datetime] = None

    @property
    def state(self) -> OrderState:
        return self.status

    def transition_to(self, target_state: OrderState, reason: Optional[str] = None) -> None:
        """Advance order to target state if legal, or raise ValueError."""
        allowed = _LEGAL_TRANSITIONS.get(self.status, set())
        if target_state not in allowed:
            raise ValueError(
                f"Illegal order state transition: cannot advance order {self.order_id} from {self.status.value} to {target_state.value}."
            )
        self.status = target_state
        if reason:
            self.rejection_reason = reason
        if target_state == OrderState.FILLED:
            self.filled_at = datetime.now(timezone.utc)
        logger.debug(f"Order {self.order_id} transitioned to {target_state.value}")


class MatchingEngine:
    """Virtual Order Matching Engine implementing Phase 1 execution fill and cost semantics."""

    def __init__(self, default_slippage_usd: float = 0.10, fixed_slippage_usd: Optional[float] = None):
        self.default_slippage_usd = fixed_slippage_usd if fixed_slippage_usd is not None else default_slippage_usd


    def calculate_entry_fill(
        self,
        quote_price: float,
        direction: str,
        quantity: float,
        spread: float,
        slippage: Optional[float] = None,
        spec: Optional[InstrumentSpecification] = None,
    ) -> Tuple[float, float, float, float]:
        """Calculate entry fill price, spread, slippage, and fee matching Phase 1 semantics.

        Long pays half-spread + slippage upward.
        Short sells into bid (half-spread + slippage downward).
        Returns (fill_price, half_spread_cost, slippage_cost, fee_cost).
        """
        half_spread = spread / 2.0
        slip = slippage if slippage is not None else self.default_slippage_usd

        if direction.upper() == "LONG":
            fill_price = quote_price + half_spread + slip
        else:
            fill_price = quote_price - half_spread - slip

        fee = spec.calculate_fee(fill_price, quantity, is_maker=False) if spec else 0.0
        return fill_price, half_spread, slip, fee

    def calculate_exit_fill(
        self,
        quote_price: float,
        direction: str,
        quantity: float,
        spread: float,
        slippage: Optional[float] = None,
        spec: Optional[InstrumentSpecification] = None,
    ) -> Tuple[float, float, float, float]:
        """Calculate exit fill price, spread, slippage, and fee matching Phase 1 semantics.

        Exiting Long means selling into bid: quote - half_spread - slippage.
        Exiting Short means buying from ask: quote + half_spread + slippage.
        Returns (fill_price, half_spread_cost, slippage_cost, fee_cost).
        """
        half_spread = spread / 2.0
        slip = slippage if slippage is not None else self.default_slippage_usd

        if direction.upper() == "LONG":
            fill_price = quote_price - half_spread - slip
        else:
            fill_price = quote_price + half_spread + slip

        fee = spec.calculate_fee(fill_price, quantity, is_maker=False) if spec else 0.0
        return fill_price, half_spread, slip, fee
