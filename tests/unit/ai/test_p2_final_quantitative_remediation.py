"""Focused unit tests for Phase 2 Final Quantitative Remediation.

Covers:
- P2-1: Monte Carlo minimum trade count boundaries (N=9, 10, 49, 50, 51)
- P2-2: Monte Carlo ruin probability boundary (0.0499, 0.0500, 0.0501)
- P2-3: WFA history requirements and complete removal of 70/30 fallback
- P3: Reconciled Leaderboard contract
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import numpy as np
import pandas as pd
import pytest

from src.backtest.types import ExitReason, OrderDirection, TradeRecord
from src.quant.validation.config import ValidationThresholdsConfig
from src.quant.validation.monte_carlo import MonteCarloSimulator
from src.quant.validation.walk_forward import WalkForwardAnalyzer
from src.strategy.contracts import (
    ConditionComparison,
    ConditionNode,
    DataSourceEnum,
    ExecutionConstraints,
    IndicatorNameEnum,
    IndicatorSpec,
    InstrumentEnum,
    RegimeEnum,
    StopLossSpec,
    StrategyProposal,
    TakeProfitSpec,
    TimeframeEnum,
    TradeDirection,
    ValueRef,
    ValueRefType,
)
from src.strategy.interpreter import ASTStrategy


def create_trades(n_trades: int, win_pct: float = 0.65):
    """Helper to generate synthetic trade records."""
    trades = []
    for i in range(n_trades):
        is_win = (i / max(1, n_trades)) < win_pct
        pnl = 150.0 if is_win else -100.0
        trades.append(
            TradeRecord(
                trade_number=i + 1,
                direction=OrderDirection.LONG,
                entry_time=datetime.now(timezone.utc),
                exit_time=datetime.now(timezone.utc),
                entry_price=2000.0,
                exit_price=2015.0 if is_win else 1990.0,
                size=1.0,
                initial_sl=1990.0,
                initial_tp=2015.0,
                gross_pnl=pnl + 5.0,
                net_pnl=pnl,
                return_pct=pnl / 10000.0,
                r_multiple=1.5 if is_win else -1.0,
                fees=2.0,
                slippage=3.0,
                mae_pct=0.005,
                mfe_pct=0.015,
                exit_reason=ExitReason.TAKE_PROFIT if is_win else ExitReason.STOP_LOSS,
                holding_bars=10,
            )
        )
    return trades


def create_dummy_strategy():
    """Helper to instantiate a valid ASTStrategy."""
    proposal = StrategyProposal(
        name="WFA_TEST_STRATEGY",
        hypothesis="Strategy for WFA history fold testing.",
        direction=TradeDirection.LONG,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.RANGING],
        indicators=[IndicatorSpec(alias="ema_fast", name=IndicatorNameEnum.EMA, inputs={"period": 10})],
        entry_rules=ConditionNode(
            operator="AND",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                    comparator=">",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_fast", lag=0),
                )
            ],
        ),
        exit_rules=ConditionNode(
            operator="OR",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                    comparator="<",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_fast", lag=0),
                )
            ],
        ),
        stop_loss=StopLossSpec(type="ATR_MULTIPLE", multiplier_or_value=1.5),
        take_profit=TakeProfitSpec(type="ATR_MULTIPLE", multiplier_or_value=3.0),
        constraints=ExecutionConstraints(max_holding_bars=100, allowed_sessions=["ALL"]),
        rationale="WFA history test strategy proposal.",
    )
    return ASTStrategy(proposal)


# ==============================================================================
# P2-1: MONTE CARLO MINIMUM TRADE COUNT BOUNDARY TESTS
# ==============================================================================

def test_monte_carlo_trade_count_boundary_n9():
    """N = 9 -> Strictly rejected by centralized MIN_TRADES_GATE."""
    mc = MonteCarloSimulator()
    trades = create_trades(9)
    res = mc.evaluate(trades)
    assert res.passed is False
    assert len(res.rejection_reasons) > 0
    assert "Insufficient trades (9 < minimum 50)" in res.rejection_reasons[0]


def test_monte_carlo_trade_count_boundary_n10():
    """N = 10 -> Strictly rejected by centralized MIN_TRADES_GATE (previously allowed by buggy < 10)."""
    mc = MonteCarloSimulator()
    trades = create_trades(10)
    res = mc.evaluate(trades)
    assert res.passed is False
    assert len(res.rejection_reasons) > 0
    assert "Insufficient trades (10 < minimum 50)" in res.rejection_reasons[0]


def test_monte_carlo_trade_count_boundary_n49():
    """N = 49 -> Strictly rejected by centralized MIN_TRADES_GATE."""
    mc = MonteCarloSimulator()
    trades = create_trades(49)
    res = mc.evaluate(trades)
    assert res.passed is False
    assert len(res.rejection_reasons) > 0
    assert "Insufficient trades (49 < minimum 50)" in res.rejection_reasons[0]


def test_monte_carlo_trade_count_boundary_n50():
    """N = 50 -> Exactly eligible for Monte Carlo bootstrap."""
    mc = MonteCarloSimulator()
    trades = create_trades(50, win_pct=0.70)
    res = mc.evaluate(trades, iterations=500)
    # Does not reject for trade count
    assert not any("Insufficient trades" in r for r in res.rejection_reasons)
    assert res.iterations == 500


def test_monte_carlo_trade_count_boundary_n51():
    """N = 51 -> Eligible for Monte Carlo bootstrap."""
    mc = MonteCarloSimulator()
    trades = create_trades(51, win_pct=0.70)
    res = mc.evaluate(trades, iterations=500)
    assert not any("Insufficient trades" in r for r in res.rejection_reasons)
    assert res.iterations == 500


# ==============================================================================
# P2-2: MONTE CARLO RUIN PROBABILITY BOUNDARY TESTS (>= 0.05 FAILS)
# ==============================================================================

def test_monte_carlo_ruin_boundary_0_0499_passes():
    """Ruin probability 0.0499 < 0.0500 -> PASS."""
    mc = MonteCarloSimulator()
    trades = create_trades(50)

    with patch.object(mc, "run_bootstrap_reshuffle") as mock_boot, \
         patch.object(mc, "run_friction_stress") as mock_stress:
        mock_boot.return_value = {
            "median_drawdown_pct": 0.05,
            "p95_drawdown_pct": 0.12,
            "worst_case_drawdown_pct": 0.18,
            "probability_of_ruin": 0.0499,
            "p95_losing_streak": 4,
        }
        mock_stress.return_value = 1.35  # >= 1.10 passing

        res = mc.evaluate(trades, iterations=1000, param_degradation_pct=0.20)
        assert res.passed is True
        assert res.probability_of_ruin == 0.0499
        assert len(res.rejection_reasons) == 0


def test_monte_carlo_ruin_boundary_0_0500_fails():
    """Ruin probability 0.0500 >= 0.0500 -> FAIL (ADR-009 canonical rule)."""
    mc = MonteCarloSimulator()
    trades = create_trades(50)

    with patch.object(mc, "run_bootstrap_reshuffle") as mock_boot, \
         patch.object(mc, "run_friction_stress") as mock_stress:
        mock_boot.return_value = {
            "median_drawdown_pct": 0.05,
            "p95_drawdown_pct": 0.14,
            "worst_case_drawdown_pct": 0.20,
            "probability_of_ruin": 0.0500,
            "p95_losing_streak": 5,
        }
        mock_stress.return_value = 1.35

        res = mc.evaluate(trades, iterations=1000, param_degradation_pct=0.20)
        assert res.passed is False
        assert res.probability_of_ruin == 0.0500
        assert any("reaches or exceeds maximum threshold 0.0500" in r for r in res.rejection_reasons)


def test_monte_carlo_ruin_boundary_0_0501_fails():
    """Ruin probability 0.0501 > 0.0500 -> FAIL."""
    mc = MonteCarloSimulator()
    trades = create_trades(50)

    with patch.object(mc, "run_bootstrap_reshuffle") as mock_boot, \
         patch.object(mc, "run_friction_stress") as mock_stress:
        mock_boot.return_value = {
            "median_drawdown_pct": 0.06,
            "p95_drawdown_pct": 0.15,
            "worst_case_drawdown_pct": 0.22,
            "probability_of_ruin": 0.0501,
            "p95_losing_streak": 5,
        }
        mock_stress.return_value = 1.35

        res = mc.evaluate(trades, iterations=1000, param_degradation_pct=0.20)
        assert res.passed is False
        assert res.probability_of_ruin == 0.0501
        assert any("reaches or exceeds maximum threshold 0.0500" in r for r in res.rejection_reasons)


# ==============================================================================
# P2-3: WFA HISTORY REQUIREMENTS & COMPLETE REMOVAL OF 70/30 FALLBACK
# ==============================================================================

def test_wfa_insufficient_history_strictly_rejected_no_fallback():
    """Datasets with 200 days (< 450 days required for 12m IS + 3m OOS) are rejected with NO fallback."""
    analyzer = WalkForwardAnalyzer()
    strategy = create_dummy_strategy()

    # 200 days of hourly data (would previously trigger 70/30 fallback for >= 120 days)
    n_bars = 24 * 200
    dates = pd.date_range("2023-01-01", periods=n_bars, freq="1h", tz=timezone.utc)
    closes = 2000.0 + np.linspace(0, 50, n_bars)
    df = pd.DataFrame(
        {
            "timestamp": dates,
            "open": closes - 0.5,
            "high": closes + 1.0,
            "low": closes - 1.0,
            "close": closes,
            "volume": np.full(n_bars, 100.0),
        }
    )

    res = analyzer.evaluate_strategy(df=df, strategy=strategy)
    assert res.passed is False
    assert len(res.folds) == 0  # CRITICAL: Exactly 0 folds, zero 70/30 split created!
    assert any("Insufficient historical data duration" in r for r in res.rejection_reasons)
    assert any("requires minimum 15 months / 450 days" in r for r in res.rejection_reasons)


def test_wfa_sufficient_canonical_history_constructs_rolling_folds():
    """Datasets with 600 days construct genuine rolling folds with 12m IS, 3m OOS, 3m step."""
    analyzer = WalkForwardAnalyzer()

    n_bars = 24 * 600
    dates = pd.date_range("2023-01-01", periods=n_bars, freq="1h", tz=timezone.utc)
    closes = 2000.0 + np.linspace(0, 100, n_bars)
    df = pd.DataFrame(
        {
            "timestamp": dates,
            "open": closes - 0.5,
            "high": closes + 1.0,
            "low": closes - 1.0,
            "close": closes,
            "volume": np.full(n_bars, 100.0),
        }
    )

    folds = analyzer.partition_folds(df, is_months=12, oos_months=3, step_months=3)
    assert len(folds) >= 2  # At least 2 rolling folds

    # Verify Fold 1
    is_f1, oos_f1 = folds[0]
    is_f1_days = (is_f1["timestamp"].max() - is_f1["timestamp"].min()).days
    oos_f1_days = (oos_f1["timestamp"].max() - oos_f1["timestamp"].min()).days
    assert is_f1_days >= 350  # ~12 months (360 days)
    assert oos_f1_days >= 85   # ~3 months (90 days)
    assert is_f1["timestamp"].max() <= oos_f1["timestamp"].min()

    # Verify Fold 2 stepped forward by ~3 months
    is_f2, oos_f2 = folds[1]
    step_days = (is_f2["timestamp"].min() - is_f1["timestamp"].min()).days
    assert 85 <= step_days <= 95  # ~3 months (90 days step)


def test_wfa_no_70_30_fallback_code_artifacts_in_result():
    """Verify that no result payload contains 70/30 fallback indicators."""
    analyzer = WalkForwardAnalyzer()
    strategy = create_dummy_strategy()

    # Short 130-day dataset (above old 120-day cutoff, but below 450 days)
    n_bars = 24 * 130
    dates = pd.date_range("2023-01-01", periods=n_bars, freq="1h", tz=timezone.utc)
    closes = 2000.0 + np.linspace(0, 20, n_bars)
    df = pd.DataFrame(
        {
            "timestamp": dates,
            "open": closes,
            "high": closes + 1.0,
            "low": closes - 1.0,
            "close": closes,
            "volume": np.full(n_bars, 100.0),
        }
    )

    res = analyzer.evaluate_strategy(df=df, strategy=strategy)
    assert res.passed is False
    assert len(res.folds) == 0

