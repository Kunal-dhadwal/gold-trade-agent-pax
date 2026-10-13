"""Unit tests for Canonical 11-State Order Lifecycle FSM (Sub-Milestone 4.2.2)."""

import pytest
from src.execution.delta.fsm import (
    ACTIVE_STATES,
    EVENT_TRANSITIONS,
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    InvalidStateTransitionError,
    OrderEvent,
    OrderLifecycleFSM,
    OrderState,
)


def test_fsm_initial_state():
    """Verify initial default state and properties of OrderLifecycleFSM."""
    fsm = OrderLifecycleFSM(order_id="ORD-001", client_order_id="AGY_TEST_123")
    assert fsm.state == OrderState.CREATED
    assert not fsm.is_terminal
    assert not fsm.is_active
    assert len(fsm.history) == 0


def test_terminal_and_active_state_membership():
    """Verify exact categorization of terminal and active states."""
    expected_terminals = {
        OrderState.FILLED,
        OrderState.REJECTED,
        OrderState.CANCELLED,
        OrderState.EXPIRED,
    }
    assert TERMINAL_STATES == expected_terminals

    expected_actives = {
        OrderState.SUBMITTED,
        OrderState.ACCEPTED,
        OrderState.PARTIALLY_FILLED,
        OrderState.RECONCILING,
    }
    assert ACTIVE_STATES == expected_actives

    for state in OrderState:
        fsm = OrderLifecycleFSM(order_id="ORD-CHECK", initial_state=state)
        assert fsm.is_terminal == (state in expected_terminals)
        assert fsm.is_active == (state in expected_actives)


@pytest.mark.parametrize("from_state, to_state", [
    (OrderState.CREATED, OrderState.SIGNED),
    (OrderState.CREATED, OrderState.REJECTED),
    (OrderState.SIGNED, OrderState.SUBMITTED),
    (OrderState.SUBMITTED, OrderState.ACCEPTED),
    (OrderState.SUBMITTED, OrderState.REJECTED),
    (OrderState.SUBMITTED, OrderState.FAILED),
    (OrderState.SUBMITTED, OrderState.FILLED),
    (OrderState.SUBMITTED, OrderState.PARTIALLY_FILLED),
    (OrderState.SUBMITTED, OrderState.CANCELLED),
    (OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED),
    (OrderState.ACCEPTED, OrderState.FILLED),
    (OrderState.ACCEPTED, OrderState.CANCELLED),
    (OrderState.ACCEPTED, OrderState.EXPIRED),
    (OrderState.PARTIALLY_FILLED, OrderState.FILLED),
    (OrderState.PARTIALLY_FILLED, OrderState.CANCELLED),
    (OrderState.PARTIALLY_FILLED, OrderState.EXPIRED),
    (OrderState.FAILED, OrderState.RECONCILING),
    (OrderState.RECONCILING, OrderState.ACCEPTED),
    (OrderState.RECONCILING, OrderState.FILLED),
    (OrderState.RECONCILING, OrderState.PARTIALLY_FILLED),
    (OrderState.RECONCILING, OrderState.CANCELLED),
    (OrderState.RECONCILING, OrderState.EXPIRED),
    (OrderState.RECONCILING, OrderState.REJECTED),
])
def test_all_valid_fsm_transitions(from_state: OrderState, to_state: OrderState):
    """Verify that every valid transition in the 22-edge transition matrix succeeds."""
    fsm = OrderLifecycleFSM(order_id="ORD-VALID", initial_state=from_state)
    assert fsm.can_transition_to(to_state)

    new_state = fsm.transition(to_state, payload={"test_key": "test_val"})
    assert new_state == to_state
    assert fsm.state == to_state
    assert len(fsm.history) == 1

    entry = fsm.history[0]
    assert entry["order_id"] == "ORD-VALID"
    assert entry["from_state"] == from_state.value
    assert entry["to_state"] == to_state.value
    assert entry["payload"] == {"test_key": "test_val"}
    assert entry["timestamp"] is not None


def test_exhaustive_invalid_transitions():
    """Verify that every transition not in the valid transition matrix raises InvalidStateTransitionError."""
    tested_count = 0
    for from_state in OrderState:
        valid_targets = VALID_TRANSITIONS.get(from_state, set())
        for to_state in OrderState:
            if to_state not in valid_targets:
                fsm = OrderLifecycleFSM(order_id="ORD-INVALID", initial_state=from_state)
                assert not fsm.can_transition_to(to_state)
                with pytest.raises(InvalidStateTransitionError):
                    fsm.transition(to_state)
                tested_count += 1

    # Total ordered pairs: 11 * 11 = 121
    # Valid transitions: 23
    # Invalid transitions: 121 - 23 = 98
    total_valid = sum(len(targets) for targets in VALID_TRANSITIONS.values())
    assert tested_count == (len(OrderState) * len(OrderState) - total_valid)


def test_terminal_states_cannot_transition():
    """Verify that terminal states (FILLED, REJECTED, CANCELLED, EXPIRED) reject all outgoing transitions."""
    for terminal_state in TERMINAL_STATES:
        fsm = OrderLifecycleFSM(order_id="ORD-TERM", initial_state=terminal_state)
        assert fsm.is_terminal
        for target in OrderState:
            with pytest.raises(InvalidStateTransitionError):
                fsm.transition(target)


def test_fsm_helper_methods():
    """Verify expressive transition helper methods on OrderLifecycleFSM."""
    # Lifecycle path A: CREATED -> SIGNED -> SUBMITTED -> ACCEPTED -> PARTIALLY_FILLED -> FILLED
    fsm_a = OrderLifecycleFSM(order_id="ORD-A")
    assert fsm_a.sign() == OrderState.SIGNED
    assert fsm_a.submit() == OrderState.SUBMITTED
    assert fsm_a.ack() == OrderState.ACCEPTED
    assert fsm_a.partial_fill() == OrderState.PARTIALLY_FILLED
    assert fsm_a.fill() == OrderState.FILLED
    assert fsm_a.is_terminal
    assert len(fsm_a.history) == 5

    # Lifecycle path B: CREATED -> SIGNED -> SUBMITTED -> FAILED -> RECONCILING -> ACCEPTED -> CANCELLED
    fsm_b = OrderLifecycleFSM(order_id="ORD-B")
    fsm_b.sign()
    fsm_b.submit()
    assert fsm_b.fail() == OrderState.FAILED
    assert fsm_b.escalate_reconciling() == OrderState.RECONCILING
    assert fsm_b.ack() == OrderState.ACCEPTED
    assert fsm_b.cancel() == OrderState.CANCELLED
    assert fsm_b.is_terminal

    # Lifecycle path C: CREATED -> REJECTED
    fsm_c = OrderLifecycleFSM(order_id="ORD-C")
    assert fsm_c.reject() == OrderState.REJECTED
    assert fsm_c.is_terminal

    # Lifecycle path D: ACCEPTED -> EXPIRED
    fsm_d = OrderLifecycleFSM(order_id="ORD-D", initial_state=OrderState.ACCEPTED)
    assert fsm_d.expire() == OrderState.EXPIRED
    assert fsm_d.is_terminal


def test_invalid_event_trigger():
    """Verify trigger_event raises InvalidStateTransitionError when event is illegal."""
    fsm = OrderLifecycleFSM(order_id="ORD-EVT", initial_state=OrderState.CREATED)
    with pytest.raises(InvalidStateTransitionError):
        fsm.trigger_event(OrderEvent.FILL)
