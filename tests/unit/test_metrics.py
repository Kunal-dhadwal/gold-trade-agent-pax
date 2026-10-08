"""Unit tests for quantitative metrics engine."""

from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest
from src.core.constants import OrderDirection, ExitReason
from src.backtest.types import TradeRecord
from src.backtest.metrics import MetricsEngine


def test_metrics_calculation_accuracy():
    # 1 winning trade, 1 losing trade
    t1 = TradeRecord(
        trade_number=1,
        direction=OrderDirection.LONG,
        entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        exit_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
        entry_price=2000.0,
        exit_price=2040.0,
        size=1.0,
        initial_sl=1980.0,
        initial_tp=2040.0,
        gross_pnl=40.0,
        net_pnl=38.0,
        return_pct=0.38,
        r_multiple=1.9,
        fees=1.0,
        slippage=1.0,
        mae_pct=0.5,
        mfe_pct=2.0,
        exit_reason=ExitReason.TAKE_PROFIT,
        holding_bars=24,
    )
    t2 = TradeRecord(
        trade_number=2,
        direction=OrderDirection.LONG,
        entry_time=datetime(2024, 1, 3, tzinfo=timezone.utc),
        exit_time=datetime(2024, 1, 4, tzinfo=timezone.utc),
        entry_price=2030.0,
        exit_price=2010.0,
        size=1.0,
        initial_sl=2010.0,
        initial_tp=2070.0,
        gross_pnl=-20.0,
        net_pnl=-22.0,
        return_pct=-0.22,
        r_multiple=-1.1,
        fees=1.0,
        slippage=1.0,
        mae_pct=1.0,
        mfe_pct=0.2,
        exit_reason=ExitReason.STOP_LOSS,
        holding_bars=24,
    )
    trades = [t1, t2]
    equity_series = pd.Series([10000.0, 10038.0, 10016.0])

    metrics = MetricsEngine.calculate_metrics(
        trades=trades,
        equity_series=equity_series,
        initial_capital=10000.0,
    )

    assert metrics.total_trades == 2
    assert metrics.winning_trades == 1
    assert metrics.losing_trades == 1
    assert metrics.win_rate_pct == 50.0
    assert metrics.total_net_profit == 16.0
    # Gross Profit Factor = gross_gains (40.0) / gross_losses (20.0) = 2.0
    assert metrics.profit_factor == pytest.approx(40.0 / 20.0, 0.01)
    assert metrics.max_drawdown_usd == 22.0
    assert metrics.recovery_factor == pytest.approx(16.0 / 22.0, 0.01)
    assert metrics.total_fees_paid == 2.0
    assert metrics.total_slippage_cost == 2.0


def test_gross_profit_factor_zero_loss_and_all_losses():
    # Only winning trades -> profit factor capped at 99.0
    t_win = TradeRecord(
        trade_number=1,
        direction=OrderDirection.LONG,
        entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        exit_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
        entry_price=2000.0,
        exit_price=2050.0,
        size=1.0,
        initial_sl=1980.0,
        initial_tp=2050.0,
        gross_pnl=50.0,
        net_pnl=48.0,
        return_pct=0.48,
        r_multiple=2.5,
        fees=1.0,
        slippage=1.0,
        mae_pct=0.1,
        mfe_pct=2.5,
        exit_reason=ExitReason.TAKE_PROFIT,
        holding_bars=10,
    )
    metrics_win = MetricsEngine.calculate_metrics(
        trades=[t_win],
        equity_series=pd.Series([10000.0, 10048.0]),
        initial_capital=10000.0,
    )
    assert metrics_win.profit_factor == 99.0

    # Only losing trades -> profit factor 0.0
    t_loss = TradeRecord(
        trade_number=1,
        direction=OrderDirection.SHORT,
        entry_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        exit_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
        entry_price=2000.0,
        exit_price=2020.0,
        size=1.0,
        initial_sl=2020.0,
        initial_tp=1950.0,
        gross_pnl=-20.0,
        net_pnl=-22.0,
        return_pct=-0.22,
        r_multiple=-1.0,
        fees=1.0,
        slippage=1.0,
        mae_pct=1.0,
        mfe_pct=0.0,
        exit_reason=ExitReason.STOP_LOSS,
        holding_bars=5,
    )
    metrics_loss = MetricsEngine.calculate_metrics(
        trades=[t_loss],
        equity_series=pd.Series([10000.0, 9978.0]),
        initial_capital=10000.0,
    )
    assert metrics_loss.profit_factor == 0.0


def test_sortino_ratio_institutional_formula():
    """Verify Sortino uses full sample denominator N, not n_downside."""
    # Equity curve: [100.0, 105.0, 102.0, 108.0, 104.0]
    # Returns R:
    # R1 = +0.05
    # R2 = -0.02857142857
    # R3 = +0.05882352941
    # R4 = -0.03703703704
    equity = pd.Series([100.0, 105.0, 102.0, 108.0, 104.0])
    returns = equity.pct_change().dropna().values
    N = len(returns)  # 4
    mean_r = float(np.mean(returns))

    # Downside diff: only negative returns squared, positive returns are 0
    downside_diff = np.minimum(0.0, returns)
    downside_var = float(np.mean(downside_diff ** 2))  # divided by N, NOT count of negative returns!
    downside_std = np.sqrt(downside_var)

    timeframe_minutes = 60
    bars_per_day = 24
    annual_factor = np.sqrt(252 * bars_per_day)
    expected_sortino = float((mean_r / downside_std) * annual_factor)

    metrics = MetricsEngine.calculate_metrics(
        trades=[],
        equity_series=equity,
        initial_capital=100.0,
        timeframe_minutes=timeframe_minutes,
    )

    assert metrics.sortino_ratio == pytest.approx(expected_sortino, rel=1e-5)


def test_sortino_ratio_all_positive_returns():
    """All positive returns have zero downside deviation, capped at 99.0 without error."""
    equity = pd.Series([100.0, 102.0, 105.0, 110.0, 115.0])
    metrics = MetricsEngine.calculate_metrics(
        trades=[],
        equity_series=equity,
        initial_capital=100.0,
    )
    assert metrics.sortino_ratio == 99.0


def test_sortino_ratio_all_negative_returns():
    """All negative returns produce negative Sortino ratio."""
    equity = pd.Series([100.0, 98.0, 95.0, 91.0, 88.0])
    metrics = MetricsEngine.calculate_metrics(
        trades=[],
        equity_series=equity,
        initial_capital=100.0,
    )
    assert metrics.sortino_ratio < 0.0
    assert not np.isnan(metrics.sortino_ratio)
    assert not np.isinf(metrics.sortino_ratio)


def test_sortino_ratio_zero_variance():
    """Constant equity produces 0.0 Sortino ratio."""
    equity = pd.Series([100.0, 100.0, 100.0, 100.0])
    metrics = MetricsEngine.calculate_metrics(
        trades=[],
        equity_series=equity,
        initial_capital=100.0,
    )
    assert metrics.sortino_ratio == 0.0


def test_sortino_ratio_empty_returns():
    """Single-point or empty equity produces 0.0 Sortino ratio."""
    equity = pd.Series([100.0])
    metrics = MetricsEngine.calculate_metrics(
        trades=[],
        equity_series=equity,
        initial_capital=100.0,
    )
    assert metrics.sortino_ratio == 0.0
    assert metrics.sharpe_ratio == 0.0
