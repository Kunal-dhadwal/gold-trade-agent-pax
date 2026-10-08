"""Unit tests for strategies and registry."""

import numpy as np
import pandas as pd
import pytest
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
