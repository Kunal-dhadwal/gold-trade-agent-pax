"""Canonical 11-State Order Lifecycle Finite State Machine (FSM) for Live Execution.

Enforces deterministic order lifecycle transitions, terminal states, active states,
and transition audit logs adhering to Phase 4 Milestone 4.2 Section D.1-D.2.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


class OrderState(str, Enum):
    """Canonical eleven (11) states for live order execution."""
    CREATED = "CREATED"
    SIGNED = "SIGNED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    RECONCILING = "RECONCILING"


class OrderEvent(str, Enum):
    """Order transition events driving the state machine."""
    SIGN = "SIGN"
    REJECT = "REJECT"
    SUBMIT = "SUBMIT"
    ACK = "ACK"
    FAIL = "FAIL"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILL = "FILL"
    CANCEL = "CANCEL"
    EXPIRE = "EXPIRE"
    ESCALATE_RECONCILING = "ESCALATE_RECONCILING"
    RECONCILE_ACCEPTED = "RECONCILE_ACCEPTED"
    RECONCILE_FILLED = "RECONCILE_FILLED"
    RECONCILE_PARTIALLY_FILLED = "RECONCILE_PARTIALLY_FILLED"
    RECONCILE_CANCELLED = "RECONCILE_CANCELLED"
    RECONCILE_EXPIRED = "RECONCILE_EXPIRED"
    RECONCILE_REJECTED = "RECONCILE_REJECTED"


class InvalidStateTransitionError(Exception):
    """Raised when an illegal order state transition is attempted."""
    pass


# Terminal states cannot transition to any other state
TERMINAL_STATES: Set[OrderState] = {
    OrderState.FILLED,
    OrderState.REJECTED,
    OrderState.CANCELLED,
    OrderState.EXPIRED,
}

# Active states where order remains potentially active or pending
ACTIVE_STATES: Set[OrderState] = {
    OrderState.SUBMITTED,
    OrderState.ACCEPTED,
    OrderState.PARTIALLY_FILLED,
    OrderState.RECONCILING,
}

# The complete valid transition matrix (Section D.1)
VALID_TRANSITIONS: Dict[OrderState, Set[OrderState]] = {
    OrderState.CREATED: {
        OrderState.SIGNED,
        OrderState.REJECTED,
    },
    OrderState.SIGNED: {
        OrderState.SUBMITTED,
    },
    OrderState.SUBMITTED: {
        OrderState.ACCEPTED,
        OrderState.REJECTED,
        OrderState.FAILED,
        OrderState.FILLED,
        OrderState.PARTIALLY_FILLED,
        OrderState.CANCELLED,
    },
    OrderState.ACCEPTED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.EXPIRED,
    },
    OrderState.PARTIALLY_FILLED: {
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.EXPIRED,
    },
    OrderState.FAILED: {
        OrderState.RECONCILING,
    },
    OrderState.RECONCILING: {
        OrderState.ACCEPTED,
        OrderState.FILLED,
        OrderState.PARTIALLY_FILLED,
        OrderState.CANCELLED,
        OrderState.EXPIRED,
        OrderState.REJECTED,
    },
    OrderState.FILLED: set(),
    OrderState.REJECTED: set(),
    OrderState.CANCELLED: set(),
    OrderState.EXPIRED: set(),
}

# Mapping of (from_state, event) -> to_state for event-driven dispatch
EVENT_TRANSITIONS: Dict[Tuple[OrderState, OrderEvent], OrderState] = {
    (OrderState.CREATED, OrderEvent.SIGN): OrderState.SIGNED,
    (OrderState.CREATED, OrderEvent.REJECT): OrderState.REJECTED,
    (OrderState.SIGNED, OrderEvent.SUBMIT): OrderState.SUBMITTED,
    (OrderState.SUBMITTED, OrderEvent.ACK): OrderState.ACCEPTED,
    (OrderState.SUBMITTED, OrderEvent.REJECT): OrderState.REJECTED,
    (OrderState.SUBMITTED, OrderEvent.FAIL): OrderState.FAILED,
    (OrderState.SUBMITTED, OrderEvent.FILL): OrderState.FILLED,
    (OrderState.SUBMITTED, OrderEvent.PARTIAL_FILL): OrderState.PARTIALLY_FILLED,
    (OrderState.SUBMITTED, OrderEvent.CANCEL): OrderState.CANCELLED,
    (OrderState.ACCEPTED, OrderEvent.PARTIAL_FILL): OrderState.PARTIALLY_FILLED,
    (OrderState.ACCEPTED, OrderEvent.FILL): OrderState.FILLED,
    (OrderState.ACCEPTED, OrderEvent.CANCEL): OrderState.CANCELLED,
    (OrderState.ACCEPTED, OrderEvent.EXPIRE): OrderState.EXPIRED,
    (OrderState.PARTIALLY_FILLED, OrderEvent.FILL): OrderState.FILLED,
    (OrderState.PARTIALLY_FILLED, OrderEvent.CANCEL): OrderState.CANCELLED,
    (OrderState.PARTIALLY_FILLED, OrderEvent.EXPIRE): OrderState.EXPIRED,
    (OrderState.FAILED, OrderEvent.ESCALATE_RECONCILING): OrderState.RECONCILING,
    (OrderState.RECONCILING, OrderEvent.RECONCILE_ACCEPTED): OrderState.ACCEPTED,
    (OrderState.RECONCILING, OrderEvent.ACK): OrderState.ACCEPTED,
    (OrderState.RECONCILING, OrderEvent.RECONCILE_FILLED): OrderState.FILLED,
    (OrderState.RECONCILING, OrderEvent.FILL): OrderState.FILLED,
    (OrderState.RECONCILING, OrderEvent.RECONCILE_PARTIALLY_FILLED): OrderState.PARTIALLY_FILLED,
    (OrderState.RECONCILING, OrderEvent.PARTIAL_FILL): OrderState.PARTIALLY_FILLED,
    (OrderState.RECONCILING, OrderEvent.RECONCILE_CANCELLED): OrderState.CANCELLED,
    (OrderState.RECONCILING, OrderEvent.CANCEL): OrderState.CANCELLED,
    (OrderState.RECONCILING, OrderEvent.RECONCILE_EXPIRED): OrderState.EXPIRED,
    (OrderState.RECONCILING, OrderEvent.EXPIRE): OrderState.EXPIRED,
    (OrderState.RECONCILING, OrderEvent.RECONCILE_REJECTED): OrderState.REJECTED,
    (OrderState.RECONCILING, OrderEvent.REJECT): OrderState.REJECTED,
}


class OrderLifecycleFSM:
    """Canonical 11-State Finite State Machine governing live order lifecycles.
    
    Guarantees:
    - Rejection of invalid transitions via InvalidStateTransitionError.
    - Immutability of terminal states.
    - Monotonic event audit logging for every transition.
    """

    def __init__(
        self,
        order_id: str,
        client_order_id: Optional[str] = None,
        initial_state: OrderState = OrderState.CREATED,
    ):
        self.order_id = order_id
        self.client_order_id = client_order_id
        self._state: OrderState = initial_state
        self._history: List[Dict[str, Any]] = []

    @property
    def state(self) -> OrderState:
        """Current state of the order."""
        return self._state

    @property
    def is_terminal(self) -> bool:
        """Check if order is in a final, immutable terminal state."""
        return self._state in TERMINAL_STATES

    @property
    def is_active(self) -> bool:
        """Check if order is currently active on matching engine or pending."""
        return self._state in ACTIVE_STATES

    @property
    def history(self) -> List[Dict[str, Any]]:
        """Transition audit history."""
        return list(self._history)

    def can_transition_to(self, target: OrderState) -> bool:
        """Check whether transition to target state is legally permissible."""
        return target in VALID_TRANSITIONS.get(self._state, set())

    def transition(
        self,
        to_state: OrderState,
        event: Optional[OrderEvent] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> OrderState:
        """Transition order to target state with validation and audit logging.
        
        Raises:
            InvalidStateTransitionError: If the transition is illegal or from a terminal state.
        """
        if not self.can_transition_to(to_state):
            raise InvalidStateTransitionError(
                f"Invalid order transition: cannot transition order '{self.order_id}' "
                f"from {self._state.value} to {to_state.value}"
            )

        from_state = self._state
        self._state = to_state

        event_type = event.value if event else f"TRANSITION_TO_{to_state.value}"
        audit_record = {
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "from_state": from_state.value,
            "to_state": to_state.value,
            "event_type": event_type,
            "payload": payload or {},
            "timestamp": datetime.now(timezone.utc),
        }
        self._history.append(audit_record)
        return self._state

    def trigger_event(
        self,
        event: OrderEvent,
        payload: Optional[Dict[str, Any]] = None,
    ) -> OrderState:
        """Transition via named OrderEvent."""
        target_state = EVENT_TRANSITIONS.get((self._state, event))
        if target_state is None:
            raise InvalidStateTransitionError(
                f"Event '{event.value}' is invalid for order '{self.order_id}' in state {self._state.value}"
            )
        return self.transition(to_state=target_state, event=event, payload=payload)

    # Expressive transition helpers
    def sign(self, payload: Optional[Dict[str, Any]] = None) -> OrderState:
        return self.trigger_event(OrderEvent.SIGN, payload)

    def submit(self, payload: Optional[Dict[str, Any]] = None) -> OrderState:
        return self.trigger_event(OrderEvent.SUBMIT, payload)

    def ack(self, payload: Optional[Dict[str, Any]] = None) -> OrderState:
        return self.trigger_event(OrderEvent.ACK, payload)

    def fail(self, payload: Optional[Dict[str, Any]] = None) -> OrderState:
        return self.trigger_event(OrderEvent.FAIL, payload)

    def partial_fill(self, payload: Optional[Dict[str, Any]] = None) -> OrderState:
        return self.trigger_event(OrderEvent.PARTIAL_FILL, payload)

    def fill(self, payload: Optional[Dict[str, Any]] = None) -> OrderState:
        return self.trigger_event(OrderEvent.FILL, payload)

    def cancel(self, payload: Optional[Dict[str, Any]] = None) -> OrderState:
        return self.trigger_event(OrderEvent.CANCEL, payload)

    def expire(self, payload: Optional[Dict[str, Any]] = None) -> OrderState:
        return self.trigger_event(OrderEvent.EXPIRE, payload)

    def reject(self, payload: Optional[Dict[str, Any]] = None) -> OrderState:
        return self.trigger_event(OrderEvent.REJECT, payload)

    def escalate_reconciling(self, payload: Optional[Dict[str, Any]] = None) -> OrderState:
        return self.trigger_event(OrderEvent.ESCALATE_RECONCILING, payload)
