"""Unit tests for BacktestPaperParityTracker and PaperPromotionEvaluator."""

from datetime import datetime, timezone
import pytest
from src.paper.parity import BacktestPaperParityTracker, ParityStatus
from src.paper.promotion import PaperPromotionEvaluator


def test_parity_tracker_perfect_match():
    tracker = BacktestPaperParityTracker()
    now = datetime.now(timezone.utc).isoformat()

    trades = [
        {
            "trade_id": "T1",
            "signal_time": now,
            "signal_direction": "LONG",
            "entry_time": now,
            "entry_price": 2700.0,
            "exit_time": now,
            "exit_price": 2720.0,
            "quantity": 100.0,
            "fee": 1.35,
            "slippage": 0.15,
            "pnl": 20.0,
        }
    ]

    report = tracker.evaluate("strat_1", backtest_trades=trades, paper_trades=trades)

    assert report.overall_status == ParityStatus.PARITY_PASS
    assert report.is_promotable is True
    assert report.signal_congruence_pct == 100.0
    assert report.pnl_tracking_error_pct == 0.0
    assert report.trade_count_deviation_pct == 0.0
    assert len(report.dimensions) == 11


def test_parity_tracker_disqualification_on_signal_mismatch():
    tracker = BacktestPaperParityTracker()
    now = datetime.now(timezone.utc).isoformat()

    bt_trades = [
        {"signal_time": now, "signal_direction": "LONG", "entry_time": now, "entry_price": 2700.0, "exit_time": now, "exit_price": 2720.0, "quantity": 100.0, "fee": 1.0, "slippage": 0.1, "pnl": 20.0}
    ]
    paper_trades = [
        {"signal_time": now, "signal_direction": "SHORT", "entry_time": now, "entry_price": 2700.0, "exit_time": now, "exit_price": 2720.0, "quantity": 100.0, "fee": 1.0, "slippage": 0.1, "pnl": -20.0}
    ]

    report = tracker.evaluate("strat_1", backtest_trades=bt_trades, paper_trades=paper_trades)
    assert report.overall_status == ParityStatus.PARITY_FAILED
    assert report.is_promotable is False
    assert len(report.disqualification_reasons) > 0


def test_promotion_evaluator_dual_stage_success_and_ai_denial():
    evaluator = PaperPromotionEvaluator()
    parity_tracker = BacktestPaperParityTracker()

    trades = [{"signal_time": "1", "signal_direction": "LONG", "entry_time": "1", "entry_price": 2700.0, "exit_time": "1", "exit_price": 2720.0, "quantity": 10.0, "fee": 1.0, "slippage": 0.1, "pnl": 20.0}] * 55
    parity_rep = parity_tracker.evaluate("strat_1", trades, trades)

    stage1 = {
        "trade_count": 55,
        "wfe_pct": 65.0,
        "ruin_prob_pct": 2.0,
        "css": 85.0,
        "holdout_sharpe": 1.4,
    }
    stage2 = {
        "continuous_weeks": 4.5,
        "trade_count": 55,
        "expectancy_usd": 15.0,
        "profit_factor": 1.5,
        "max_drawdown_pct": 6.0,
    }

    report = evaluator.evaluate(
        strategy_id="strat_1",
        stage1_metrics=stage1,
        paper_metrics=stage2,
        parity_report=parity_rep,
        risk_incidents=0,
    )

    assert report.stage1_passed is True
    assert report.stage2_passed is True
    assert report.eligible_for_live_candidate is True

    # SECURITY TEST: AI agent must be unconditionally rejected
    with pytest.raises(PermissionError) as exc_info:
        evaluator.sign_off_promotion(
            report=report,
            operator_id="ai_research_agent_v1",
            operator_secret="secret123",
            is_ai_caller=True,
        )
    assert "ZERO authority" in str(exc_info.value)

    # Human sign-off success
    result = evaluator.sign_off_promotion(
        report=report,
        operator_id="HUMAN_HEAD_QUANT_01",
        operator_secret="super_secret_institutional_key",
        is_ai_caller=False,
    )
    assert result["status"] == "PROMOTED_TO_LIVE_CANDIDATE"
    assert result["new_state"] == "LIVE_CANDIDATE"
    assert len(result["signature"]) == 64

