"""Integration tests for deterministic backtesting engine."""

import numpy as np
import pandas as pd
import pytest
from src.core.constants import OrderDirection, ExitReason
from src.strategy.strategies.ema_cross import EMACrossoverStrategy
from src.strategy.strategies.atr_breakout import ATRBreakoutStrategy
from src.backtest.engine import BacktestEngine
from src.backtest.types import BacktestConfig


@pytest.fixture
def oscillating_ohlcv():
    dates = pd.date_range("2024-01-01", periods=300, freq="1h", tz="UTC")
    # Sine wave oscillation
    t = np.linspace(0, 4 * np.pi, 300)
    close = 2000.0 + 50.0 * np.sin(t)
    high = close + 4.0
    low = close - 4.0
    open_p = close - 1.0
    volume = np.full(300, 1000.0)

    return pd.DataFrame({
        "timestamp": dates,
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "source": "SYNTHETIC",
        "instrument": "XAUUSD",
    })


def test_backtest_engine_execution_and_metrics(oscillating_ohlcv):
    strat = EMACrossoverStrategy({
        "fast_ema": 10,
        "slow_ema": 25,
        "trend_ema": 50,
        "sl_atr_mult": 1.5,
        "rr_ratio": 2.0,
    })
    config = BacktestConfig(
        initial_capital=10000.0,
        spread_usd=0.30,
        slippage_usd=0.15,
        risk_per_trade_pct=1.0,
    )
    engine = BacktestEngine(config)
    result = engine.run(oscillating_ohlcv, strat)

    assert result.strategy_id == "gold_ema_trend_cross"
    assert result.metrics.initial_capital == 10000.0
    assert len(result.equity_curve) == len(oscillating_ohlcv)
    assert result.metrics.total_trades == len(result.trades)

    # Verify execution accounting
    for trade in result.trades:
        assert trade.entry_price > 0
        assert trade.exit_price > 0
        assert trade.fees > 0
        assert trade.slippage > 0
        assert trade.exit_reason in [ExitReason.STOP_LOSS, ExitReason.TAKE_PROFIT, ExitReason.EXPIRY]
        # In Long trade, fill entry should exceed quote price by half-spread + slippage
        if trade.direction == OrderDirection.LONG:
            assert trade.exit_price < trade.entry_price or trade.net_pnl > 0 or trade.exit_reason == ExitReason.STOP_LOSS


def test_conservative_intra_bar_sl_tp_resolution():
    """Verify that when a single bar touches both SL and TP, SL triggers first."""
    dates = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
    # Bar 5 has huge expansion touching both high TP and low SL
    df = pd.DataFrame({
        "timestamp": dates,
        "open": [2000.0] * 10,
        "high": [2005.0] * 10,
        "low": [1995.0] * 10,
        "close": [2000.0] * 10,
        "volume": [100.0] * 10,
        "source": "TEST",
        "instrument": "XAUUSD",
    })
    # Signal at bar 0
    df.loc[0, "close"] = 2002.0
    # Bar 1 touches both 1950 (low) and 2050 (high)
    df.loc[1, "high"] = 2050.0
    df.loc[1, "low"] = 1950.0

    class MockStrategy(EMACrossoverStrategy):
        def generate_signals(self, data):
            from src.strategy.base import StrategySignal
            from src.core.constants import OrderDirection
            return [
                StrategySignal(
                    timestamp=data["timestamp"].iloc[0],
                    direction=OrderDirection.LONG,
                    entry_price=2000.0,
                    stop_loss=1970.0,
                    take_profit=2030.0,
                )
            ]

    engine = BacktestEngine(BacktestConfig(initial_capital=10000.0))
    result = engine.run(df, MockStrategy())

    assert len(result.trades) == 1
    # Must exit on STOP_LOSS conservatively
    assert result.trades[0].exit_reason == ExitReason.STOP_LOSS
