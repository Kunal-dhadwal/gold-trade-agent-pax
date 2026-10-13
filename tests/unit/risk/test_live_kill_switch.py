"""Unit tests for Decoupled Institutional LiveKillSwitchFSM (Sub-Milestone 4.2.5).

Verifies:
1. Formal 6-state lifecycle and state machine graph transitions.
2. Rejection of invalid transitions with InvalidLiveKillSwitchStateTransitionError.
3. Ghost exposure handling: transitions to MANUAL_RESET_REQUIRED with ZERO automated liquidation.
4. Drawdown emergency flatten: Gate 1 (>= 6.0%) transitions to FLATTENING.
5. Flattening completion: FLATTENING -> FLATTENED -> MANUAL_RESET_REQUIRED.
6. Reset authorization:
   - Strict AI principal lockout (HTTP 403 / Security Veto).
   - ADMIN role authorization enforcement.
   - Reconciliation discrepancy gate (DiscrepancyCount == 0 required).
   - Reconciliation status gate (CLEAN status required).
7. Concurrent and idempotent triggers.
8. Operational state queries (is_active, can_submit_new_orders, can_submit_protective_exits).
"""

from datetime import datetime, timezone
import pytest

from src.risk.live_kill_switch import (
    LiveKillSwitchFSM,
    LiveKillSwitchState,
    InvalidLiveKillSwitchStateTransitionError,
    LiveKillSwitchSecurityError,
    LiveKillSwitchPermissionError,
    LiveKillSwitchDiscrepancyError,
)


def test_initial_state_normal():
    """Verify clean initialization in NORMAL state."""
    fsm = LiveKillSwitchFSM()
    assert fsm.state == LiveKillSwitchState.NORMAL
    assert not fsm.is_active()
    assert fsm.can_submit_new_orders()
    assert fsm.can_submit_protective_exits()
    assert fsm.triggered_at is None
    assert fsm.reset_at is None
    assert len(fsm.transition_history) == 0


def test_ghost_exposure_transition_zero_auto_liquidation():
    """Verify ghost exposure latches MANUAL_RESET_REQUIRED with zero automated liquidation."""
    fsm = LiveKillSwitchFSM()

    state = fsm.trigger_ghost_exposure(
        discrepancy_details="Ghost position 10.0 lots detected on exchange without DB record",
        triggered_by="RECONCILIATION_DAEMON",
    )

    # Invariant: Must end in MANUAL_RESET_REQUIRED
    assert state == LiveKillSwitchState.MANUAL_RESET_REQUIRED
    assert fsm.state == LiveKillSwitchState.MANUAL_RESET_REQUIRED
    assert fsm.policy == "GHOST_EXPOSURE"
    assert fsm.is_active()
    assert not fsm.can_submit_new_orders()
    # Invariant: Resting protective stops remain active
    assert fsm.can_submit_protective_exits()

    # Trace transition steps in history: NORMAL -> TRIGGERED -> NEW_TRADING_BLOCKED -> MANUAL_RESET_REQUIRED
    transitions = [t["to_state"] for t in fsm.transition_history]
    assert transitions == ["TRIGGERED", "NEW_TRADING_BLOCKED", "MANUAL_RESET_REQUIRED"]


def test_drawdown_flatten_transition():
    """Verify Gate 1 drawdown breach (>= 6.0%) transitions to FLATTENING."""
    fsm = LiveKillSwitchFSM()

    state = fsm.trigger_drawdown_flatten(drawdown_pct=6.5, triggered_by="LIVE_RISK_ENGINE")

    assert state == LiveKillSwitchState.FLATTENING
    assert fsm.state == LiveKillSwitchState.FLATTENING
    assert fsm.policy == "DRAWDOWN_FLATTEN"
    assert fsm.is_active()
    assert not fsm.can_submit_new_orders()
    assert fsm.can_submit_protective_exits()  # Reduce-only orders allowed to close position

    # Trace transitions: NORMAL -> TRIGGERED -> NEW_TRADING_BLOCKED -> FLATTENING
    transitions = [t["to_state"] for t in fsm.transition_history]
    assert transitions == ["TRIGGERED", "NEW_TRADING_BLOCKED", "FLATTENING"]


def test_flattening_completion_flow():
    """Verify FLATTENING -> FLATTENED -> MANUAL_RESET_REQUIRED lifecycle."""
    fsm = LiveKillSwitchFSM()
    fsm.trigger_drawdown_flatten(drawdown_pct=7.2)

    assert fsm.state == LiveKillSwitchState.FLATTENING

    # Positions confirmed closed by matching engine
    state = fsm.confirm_flattened(operator_id="SETTLEMENT_DAEMON")

    assert state == LiveKillSwitchState.MANUAL_RESET_REQUIRED
    assert fsm.state == LiveKillSwitchState.MANUAL_RESET_REQUIRED
    assert fsm.is_active()
    assert not fsm.can_submit_new_orders()

    # Trace: NORMAL -> TRIGGERED -> NEW_TRADING_BLOCKED -> FLATTENING -> FLATTENED -> MANUAL_RESET_REQUIRED
    transitions = [t["to_state"] for t in fsm.transition_history]
    assert transitions == [
        "TRIGGERED",
        "NEW_TRADING_BLOCKED",
        "FLATTENING",
        "FLATTENED",
        "MANUAL_RESET_REQUIRED",
    ]


def test_confirm_flattened_invalid_from_normal():
    """Verify confirm_flattened raises if not in FLATTENING state."""
    fsm = LiveKillSwitchFSM()
    with pytest.raises(InvalidLiveKillSwitchStateTransitionError):
        fsm.confirm_flattened()


def test_invalid_transitions_rejected():
    """Verify illegal transitions in the formal state graph are rejected."""
    fsm = LiveKillSwitchFSM()

    # NORMAL cannot jump directly to FLATTENED or MANUAL_RESET_REQUIRED
    with pytest.raises(InvalidLiveKillSwitchStateTransitionError):
        fsm.transition_to(LiveKillSwitchState.FLATTENED, reason="Illegal jump")

    with pytest.raises(InvalidLiveKillSwitchStateTransitionError):
        fsm.transition_to(LiveKillSwitchState.MANUAL_RESET_REQUIRED, reason="Illegal jump")

    # Move to TRIGGERED
    fsm.transition_to(LiveKillSwitchState.TRIGGERED, reason="Valid step")

    # TRIGGERED cannot jump to NORMAL or FLATTENING directly
    with pytest.raises(InvalidLiveKillSwitchStateTransitionError):
        fsm.transition_to(LiveKillSwitchState.NORMAL, reason="Illegal skip")


def test_ai_principal_reset_lockout():
    """Verify AI models and automated bots are strictly denied reset authority."""
    fsm = LiveKillSwitchFSM()
    fsm.trigger_ghost_exposure("Ghost order detected")

    ai_principals = [
        "ai_trader_v1",
        "bot_rebalancer",
        "agent_quant",
        "llm_gemini_pro",
        "service_cron",
        "daemon_auto_reset",
        "AI_SUPERVISOR",
        "bot_executor",
    ]

    for principal in ai_principals:
        with pytest.raises(LiveKillSwitchSecurityError) as exc_info:
            fsm.reset(
                operator_role="ADMIN",
                operator_id=principal,
                discrepancy_count=0,
                reconciliation_status="CLEAN",
            )
        assert "SECURITY VETO" in str(exc_info.value)
        assert fsm.state == LiveKillSwitchState.MANUAL_RESET_REQUIRED


def test_non_admin_reset_denied():
    """Verify non-ADMIN roles cannot reset the live kill switch."""
    fsm = LiveKillSwitchFSM()
    fsm.trigger_ghost_exposure("Ghost order detected")

    for role in ["OPERATOR", "VIEWER", "TRADER", "ANALYST"]:
        with pytest.raises(LiveKillSwitchPermissionError) as exc_info:
            fsm.reset(
                operator_role=role,
                operator_id="human_epps",
                discrepancy_count=0,
                reconciliation_status="CLEAN",
            )
        assert "ADMIN required" in str(exc_info.value)
        assert fsm.state == LiveKillSwitchState.MANUAL_RESET_REQUIRED


def test_discrepancy_gate_blocks_reset():
    """Verify reset is strictly blocked if discrepancy count > 0."""
    fsm = LiveKillSwitchFSM()
    fsm.trigger_ghost_exposure("Ghost position detected")

    with pytest.raises(LiveKillSwitchDiscrepancyError) as exc_info:
        fsm.reset(
            operator_role="ADMIN",
            operator_id="human_epps",
            discrepancy_count=1,
            reconciliation_status="CLEAN",
        )
    assert "unresolved discrepancy count is 1" in str(exc_info.value)
    assert fsm.state == LiveKillSwitchState.MANUAL_RESET_REQUIRED


def test_reconciliation_status_gate_blocks_reset():
    """Verify reset is blocked if reconciliation status is not CLEAN."""
    fsm = LiveKillSwitchFSM()
    fsm.trigger_ghost_exposure("Ghost position detected")

    with pytest.raises(LiveKillSwitchDiscrepancyError) as exc_info:
        fsm.reset(
            operator_role="ADMIN",
            operator_id="human_epps",
            discrepancy_count=0,
            reconciliation_status="DISCREPANT",
        )
    assert "Status must be CLEAN" in str(exc_info.value)
    assert fsm.state == LiveKillSwitchState.MANUAL_RESET_REQUIRED


def test_reset_blocked_during_active_flattening():
    """Verify reset cannot be performed while FLATTENING is actively in progress."""
    fsm = LiveKillSwitchFSM()
    fsm.trigger_drawdown_flatten(drawdown_pct=6.5)

    assert fsm.state == LiveKillSwitchState.FLATTENING

    with pytest.raises(InvalidLiveKillSwitchStateTransitionError) as exc_info:
        fsm.reset(
            operator_role="ADMIN",
            operator_id="human_epps",
            discrepancy_count=0,
            reconciliation_status="CLEAN",
        )
    assert "FLATTENING is in progress" in str(exc_info.value)
    assert fsm.state == LiveKillSwitchState.FLATTENING


def test_successful_human_admin_reset():
    """Verify valid human admin reset successfully unlocks NORMAL state."""
    fsm = LiveKillSwitchFSM()
    fsm.trigger_ghost_exposure("Ghost position detected")

    assert fsm.state == LiveKillSwitchState.MANUAL_RESET_REQUIRED

    result = fsm.reset(
        operator_role="ADMIN",
        operator_id="human_epps",
        discrepancy_count=0,
        reconciliation_status="CLEAN",
    )

    assert result is True
    assert fsm.state == LiveKillSwitchState.NORMAL
    assert not fsm.is_active()
    assert fsm.can_submit_new_orders()
    assert fsm.can_submit_protective_exits()
    assert fsm.reset_by == "human_epps"
    assert fsm.reset_at is not None


def test_idempotent_secondary_trigger_while_active():
    """Verify secondary trigger while already active appends history without corrupting state."""
    fsm = LiveKillSwitchFSM()
    fsm.trigger_ghost_exposure("First ghost")
    assert fsm.state == LiveKillSwitchState.MANUAL_RESET_REQUIRED

    initial_history_len = len(fsm.transition_history)

    # Secondary trigger occurs
    state = fsm.trigger(
        reason="Second ghost detected",
        triggered_by="RECONCILIATION_DAEMON",
        policy="GHOST_EXPOSURE",
    )

    assert state == LiveKillSwitchState.MANUAL_RESET_REQUIRED
    assert fsm.state == LiveKillSwitchState.MANUAL_RESET_REQUIRED
    assert len(fsm.transition_history) == initial_history_len + 1
    assert "SECONDARY_TRIGGER" in fsm.transition_history[-1]["reason"]


def test_get_status_serialization():
    """Verify get_status returns expected telemetry schema."""
    fsm = LiveKillSwitchFSM()
    status = fsm.get_status()

    assert status["state"] == "NORMAL"
    assert status["active"] is False
    assert status["can_submit_new_orders"] is True
    assert status["can_submit_protective_exits"] is True
    assert status["transition_count"] == 0

    fsm.trigger_ghost_exposure("Test ghost", triggered_by="TEST_RUNNER")
    active_status = fsm.get_status()

    assert active_status["state"] == "MANUAL_RESET_REQUIRED"
    assert active_status["active"] is True
    assert active_status["can_submit_new_orders"] is False
    assert active_status["can_submit_protective_exits"] is True
    assert active_status["triggered_by"] == "TEST_RUNNER"
    assert active_status["policy"] == "GHOST_EXPOSURE"
    assert active_status["transition_count"] == 3

