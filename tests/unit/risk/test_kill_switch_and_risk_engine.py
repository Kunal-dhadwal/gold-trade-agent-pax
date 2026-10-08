"""Unit tests for KillSwitchFSM and 8 Deterministic Risk Gates."""

import pytest
from src.risk.kill_switch import KillSwitchFSM, KillSwitchState
from src.risk.paper_risk_engine import PaperRiskEngine


def test_kill_switch_fsm_lifecycle():
    fsm = KillSwitchFSM()
    assert fsm.state == KillSwitchState.NORMAL
    assert fsm.is_active() is False

    # Trigger emergency halt
    final_state = fsm.trigger(reason="Test Flash Crash", triggered_by="SYSTEM")
    assert final_state == KillSwitchState.MANUAL_RESET_REQUIRED
    assert fsm.state == KillSwitchState.MANUAL_RESET_REQUIRED
    assert fsm.is_active() is True
    assert fsm.trigger_reason == "Test Flash Crash"

    # Reset requires OPERATOR or ADMIN role
    success = fsm.reset(operator_role="OPERATOR", operator_id="HUMAN_OP_1")
    assert success is True
    assert fsm.state == KillSwitchState.NORMAL
    assert fsm.is_active() is False


def test_risk_gate_drawdown_veto():
    engine = PaperRiskEngine(max_drawdown_pct=10.0)

    # Account equity dropped from $10,000 peak to $8,900 (11% drawdown)
    decision = engine.evaluate_order(
        account_equity=8900.0,
        account_cash=8900.0,
        starting_daily_equity=9500.0,
        peak_equity=10000.0,
        symbol="PAXGUSDT",
        direction="LONG",
        requested_quantity=10.0,
        entry_price=2700.0,
        stop_loss=2680.0,
        current_spread=0.20,
    )

    assert decision.approved is False
    assert decision.gate_failed == "GATE_1_MAX_DRAWDOWN"
    assert "11.00%" in decision.reason


def test_risk_gate_daily_loss_veto():
    engine = PaperRiskEngine(max_daily_loss_pct=3.0)

    # Starting day at $10,000, current equity $9,650 (3.5% daily loss)
    decision = engine.evaluate_order(
        account_equity=9650.0,
        account_cash=9650.0,
        starting_daily_equity=10000.0,
        peak_equity=10000.0,
        symbol="PAXGUSDT",
        direction="LONG",
        requested_quantity=10.0,
        entry_price=2700.0,
        stop_loss=2680.0,
        current_spread=0.20,
    )

    assert decision.approved is False
    assert decision.gate_failed == "GATE_2_DAILY_LOSS"


def test_risk_gate_spread_ratio_veto():
    engine = PaperRiskEngine(max_spread_ratio=2.0)
    # Feed historical spread of 0.20
    for _ in range(50):
        engine.update_spread_history(0.20)

    # Current spread blows out to $0.60 (3.0x rolling median)
    decision = engine.evaluate_order(
        account_equity=10000.0,
        account_cash=10000.0,
        starting_daily_equity=10000.0,
        peak_equity=10000.0,
        symbol="PAXGUSDT",
        direction="LONG",
        requested_quantity=10.0,
        entry_price=2700.0,
        stop_loss=2680.0,
        current_spread=0.60,
    )

    assert decision.approved is False
    assert decision.gate_failed == "GATE_5_SPREAD_SPIKE"



def test_risk_gate_consecutive_losses_veto():
    engine = PaperRiskEngine(max_consecutive_losses=4)

    # 4 consecutive losses reached
    decision = engine.evaluate_order(
        account_equity=10000.0,
        account_cash=10000.0,
        starting_daily_equity=10000.0,
        peak_equity=10000.0,
        symbol="PAXGUSDT",
        direction="LONG",
        requested_quantity=10.0,
        entry_price=2700.0,
        stop_loss=2680.0,
        current_spread=0.20,
        strategy_consecutive_losses=4,
    )

    assert decision.approved is False
    assert decision.gate_failed == "GATE_6_LOSS_STREAK"
