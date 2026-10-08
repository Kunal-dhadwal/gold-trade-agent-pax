"""Integration tests for deterministic backtesting engine and execution models."""

from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest
from src.core.constants import OrderDirection, ExitReason
from src.strategy.base import StrategySignal
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
        if trade.direction == OrderDirection.LONG:
            assert trade.exit_price < trade.entry_price or trade.net_pnl > 0 or trade.exit_reason == ExitReason.STOP_LOSS


def test_conservative_intra_bar_sl_tp_resolution():
    """Verify that when a single bar touches both SL and TP, SL triggers first."""
    dates = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
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


def test_next_bar_open_execution_verified():
    """Regression test: Proves Signal at T -> NO execution at T -> Fills at OPEN of T+1."""
    dates = pd.date_range("2024-01-01", periods=4, freq="1h", tz="UTC")
    # Bar 0: Signal generated at Close=2002.0
    # Bar 1: Gaps up to Open=2015.0
    # Bar 2: Reaches High=2040.0 (hits TP)
    # Bar 3: Normal
    df = pd.DataFrame({
        "timestamp": dates,
        "open": [2000.0, 2015.0, 2018.0, 2020.0],
        "high": [2005.0, 2020.0, 2040.0, 2025.0],
        "low": [1995.0, 2010.0, 2015.0, 2015.0],
        "close": [2002.0, 2018.0, 2035.0, 2022.0],
        "volume": [100.0] * 4,
        "source": "TEST",
        "instrument": "XAUUSD",
    })

    class SingleSignalStrategy(EMACrossoverStrategy):
        def generate_signals(self, data):
            return [
                StrategySignal(
                    timestamp=data["timestamp"].iloc[0],  # Signal generated at Bar 0
                    direction=OrderDirection.LONG,
                    entry_price=float(data["close"].iloc[0]),
                    stop_loss=1990.0,
                    take_profit=2035.0,
                )
            ]

    spread = 0.30
    slip = 0.15
    half_spread = spread / 2.0  # 0.15

    # Case A: execution_on_next_bar_open = False (fills on Bar 0 Close)
    cfg_close = BacktestConfig(
        initial_capital=10000.0,
        spread_usd=spread,
        slippage_usd=slip,
        execution_on_next_bar_open=False,
    )
    eng_close = BacktestEngine(cfg_close)
    res_close = eng_close.run(df, SingleSignalStrategy())

    assert len(res_close.trades) == 1
    t_close = res_close.trades[0]
    # Trade entry timestamp is Bar 0
    assert t_close.entry_time == dates[0]
    # Trade fill entry is based on Bar 0 Close (2002.0 + half_spread + slip)
    expected_close_fill = 2002.0 + half_spread + slip
    assert t_close.entry_price == pytest.approx(expected_close_fill, 0.001)

    # Case B: execution_on_next_bar_open = True (fills on Bar 1 Open)
    cfg_open = BacktestConfig(
        initial_capital=10000.0,
        spread_usd=spread,
        slippage_usd=slip,
        execution_on_next_bar_open=True,
    )
    eng_open = BacktestEngine(cfg_open)
    res_open = eng_open.run(df, SingleSignalStrategy())

    assert len(res_open.trades) == 1
    t_open = res_open.trades[0]
    # PROOF: Trade entry timestamp is Bar 1 (T+1), NOT Bar 0 (T)!
    assert t_open.entry_time == dates[1]
    # PROOF: Trade fill entry is based on Bar 1 Open (2015.0 + half_spread + slip)!
    expected_open_fill = 2015.0 + half_spread + slip
    assert t_open.entry_price == pytest.approx(expected_open_fill, 0.001)
    # The prices MUST be strictly different between the two execution models
    assert t_open.entry_price != t_close.entry_price


def test_next_bar_open_final_bar_signal_dropped():
    """Verify that a signal on the final bar has no next bar and therefore cannot execute."""
    dates = pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "timestamp": dates,
        "open": [2000.0, 2005.0, 2010.0],
        "high": [2005.0, 2010.0, 2015.0],
        "low": [1995.0, 2000.0, 2005.0],
        "close": [2002.0, 2008.0, 2012.0],
        "volume": [100.0] * 3,
        "source": "TEST",
        "instrument": "XAUUSD",
    })

    class FinalBarSignalStrategy(EMACrossoverStrategy):
        def generate_signals(self, data):
            # Signal emitted ONLY on the final bar
            return [
                StrategySignal(
                    timestamp=data["timestamp"].iloc[-1],
                    direction=OrderDirection.LONG,
                    entry_price=float(data["close"].iloc[-1]),
                    stop_loss=1990.0,
                    take_profit=2030.0,
                )
            ]

    cfg = BacktestConfig(initial_capital=10000.0, execution_on_next_bar_open=True)
    engine = BacktestEngine(cfg)
    result = engine.run(df, FinalBarSignalStrategy())

    # Pending signal on last bar had no next bar -> 0 trades executed
    assert len(result.trades) == 0


def test_no_lookahead_bias_guarantee():
    """Verify strategy signals at bar T are identical regardless of arbitrary future bar values."""
    dates = pd.date_range("2024-01-01", periods=50, freq="1h", tz="UTC")
    base_close = 2000.0 + np.cumsum(np.random.RandomState(42).randn(50))

    df_base = pd.DataFrame({
        "timestamp": dates,
        "open": base_close - 0.5,
        "high": base_close + 2.0,
        "low": base_close - 2.0,
        "close": base_close,
        "volume": [100.0] * 50,
        "source": "TEST",
        "instrument": "XAUUSD",
    })

    strat = EMACrossoverStrategy({
        "fast_ema": 10,
        "slow_ema": 20,
        "trend_ema": 30,
    })

    signals_base = strat.generate_signals(df_base)

    # Now create modified df where future bars after index 25 are drastically altered
    df_altered = df_base.copy()
    df_altered.loc[26:, "close"] = df_altered.loc[26:, "close"] * 2.0
    df_altered.loc[26:, "high"] = df_altered.loc[26:, "high"] * 2.0
    df_altered.loc[26:, "low"] = df_altered.loc[26:, "low"] * 2.0

    signals_altered = strat.generate_signals(df_altered)

    # Signals up to index 25 MUST be 100% identical in timestamp, direction, entry, SL, TP
    base_up_to_25 = [s for s in signals_base if s.timestamp <= dates[25]]
    altered_up_to_25 = [s for s in signals_altered if s.timestamp <= dates[25]]

    assert len(base_up_to_25) == len(altered_up_to_25)
    for s1, s2 in zip(base_up_to_25, altered_up_to_25):
        assert s1.timestamp == s2.timestamp
        assert s1.direction == s2.direction
        assert s1.entry_price == pytest.approx(s2.entry_price)
        assert s1.stop_loss == pytest.approx(s2.stop_loss)
        assert s1.take_profit == pytest.approx(s2.take_profit)
