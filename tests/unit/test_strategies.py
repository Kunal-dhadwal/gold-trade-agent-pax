"""Unit tests for strategies and registry."""

import numpy as np
import pandas as pd
import pytest
from src.core.constants import OrderDirection
from src.strategy.registry import StrategyRegistry
from src.strategy.strategies.ema_cross import EMACrossoverStrategy
from src.strategy.strategies.atr_breakout import ATRBreakoutStrategy
from src.strategy.strategies.rsi_reversion import RSIMeanReversionStrategy


@pytest.fixture
def trend_ohlcv():
    dates = pd.date_range("2024-01-01", periods=250, freq="1h", tz="UTC")
    # Upward trending series
    close = np.linspace(1900, 2100, 250)
    high = close + 3.0
    low = close - 3.0
    open_p = close - 0.5
    volume = np.full(250, 500.0)

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


def test_strategy_registry_catalogue():
    all_strats = StrategyRegistry.list_all()
    assert len(all_strats) >= 3
    strat_ids = [s["strategy_id"] for s in all_strats]
    assert "gold_ema_trend_cross" in strat_ids
    assert "gold_atr_breakout" in strat_ids
    assert "gold_rsi_mean_reversion" in strat_ids


def test_parameter_bounds_enforcement():
    strat = EMACrossoverStrategy({"fast_ema": 15, "slow_ema": 30})
    assert strat.get_parameter("fast_ema") == 15

    # Test parameter out of bounds raises ValueError
    with pytest.raises(ValueError):
        EMACrossoverStrategy({"fast_ema": 2})  # min is 5


def test_ema_strategy_signal_generation(trend_ohlcv):
    strat = EMACrossoverStrategy({"trend_ema": 50, "fast_ema": 10, "slow_ema": 20})
    signals = strat.generate_signals(trend_ohlcv)
    assert isinstance(signals, list)
    if signals:
        assert signals[0].stop_loss < signals[0].entry_price
        assert signals[0].take_profit > signals[0].entry_price


def test_atr_breakout_strategy_signal_generation():
    dates = pd.date_range("2024-01-01", periods=60, freq="1h", tz="UTC")
    close = np.full(60, 2000.0)
    high = np.full(60, 2005.0)
    low = np.full(60, 1995.0)
    open_p = np.full(60, 2000.0)

    # Long Breakout bar at 35
    close[35] = 2030.0
    high[35] = 2035.0
    open_p[35] = 2005.0

    # Short Breakout bar at 45
    close[45] = 1970.0
    low[45] = 1965.0
    open_p[45] = 1990.0

    df = pd.DataFrame({
        "timestamp": dates,
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.full(60, 100.0),
        "source": "SYNTHETIC",
        "instrument": "XAUUSD",
    })

    strat = ATRBreakoutStrategy({"channel_period": 20, "atr_period": 14})
    signals = strat.generate_signals(df)
    assert len(signals) == 2
    assert signals[0].direction == OrderDirection.LONG
    assert signals[0].entry_price > signals[0].stop_loss
    assert signals[0].take_profit > signals[0].entry_price
    assert signals[1].direction == OrderDirection.SHORT

    # Test short series returns empty
    assert strat.generate_signals(df.iloc[:15]) == []


def test_rsi_mean_reversion_strategy_signal_generation():
    # Construct oscillating series that creates oversold and overbought touches
    dates = pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC")
    # Sine wave oscillation
    x = np.linspace(0, 4 * np.pi, 100)
    close = 2000.0 + 30.0 * np.sin(x)
    df = pd.DataFrame({
        "timestamp": dates,
        "open": close,
        "high": close + 2.0,
        "low": close - 2.0,
        "close": close,
        "volume": np.full(100, 100.0),
        "source": "SYNTHETIC",
        "instrument": "XAUUSD",
    })

    strat = RSIMeanReversionStrategy({"rsi_period": 14, "rsi_oversold": 35.0, "rsi_overbought": 65.0})
    signals = strat.generate_signals(df)
    assert isinstance(signals, list)

    # Test short series returns empty
    assert strat.generate_signals(df.iloc[:20]) == []


def test_strategy_registry_lookup():
    strat_inst = StrategyRegistry.get("gold_ema_trend_cross")
    assert isinstance(strat_inst, EMACrossoverStrategy)

    with pytest.raises(KeyError, match="not found"):
        StrategyRegistry.get("unknown_nonexistent_strategy")


