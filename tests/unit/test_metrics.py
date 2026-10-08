"""Unit tests for quantitative metrics engine."""

from datetime import datetime, timezone
import pandas as pd
import pytest
from src.core.constants import OrderDirection, ExitReason
from src.backtest.types import TradeRecord
from src.backtest.metrics import MetricsEngine


def test_metrics_calculation_accuracy():
    # 3 winning trades, 1 losing trade
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
    assert metrics.profit_factor == pytest.approx(38.0 / 22.0, 0.01)
    assert metrics.max_drawdown_usd == 22.0
    assert metrics.recovery_factor == pytest.approx(16.0 / 22.0, 0.01)
    assert metrics.total_fees_paid == 2.0
    assert metrics.total_slippage_cost == 2.0
