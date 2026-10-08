"""Additional unit tests for PaperTradingEngine, pause/resume, and controls."""

from datetime import datetime, timezone
import pytest
from src.paper.engine import EngineState, PaperTradingEngine


@pytest.mark.asyncio
async def test_paper_engine_pause_resume_stop():
    engine = PaperTradingEngine(
        account_id="ACC_CONTROLS_TEST",
        strategy_version_id="strat_v1",
        initial_capital=10000.0,
    )
    assert engine.is_paused is False
    assert engine.is_stopped is False

    await engine.pause()
    assert engine.is_paused is True

    await engine.resume()
    assert engine.is_paused is False

    await engine.stop()
    assert engine.is_stopped is True

    # When stopped, on_candle_closed returns immediately
    bar = {"timestamp": "2026-03-15T12:00:00Z", "open": 2700.0, "high": 2710.0, "low": 2690.0, "close": 2705.0}
    await engine.on_candle_closed(bar)
    assert len(engine.candle_history) == 0


@pytest.mark.asyncio
async def test_paper_engine_short_trade_execution():
    from src.strategy.base import BaseStrategy

    class MockShortStrategy(BaseStrategy):
        strategy_id = "mock_short"
        name = "Mock Short Strategy"

        def define_parameters(self):
            return []

        def generate_signals(self, df):
            return [
                {
                    "timestamp": "2026-03-15T12:00:00Z",
                    "direction": "SHORT",
                    "entry_price": 2700.0,
                    "stop_loss": 2720.0,
                    "take_profit": 2670.0,
                    "size": 50.0,
                }
            ]

    strategy = MockShortStrategy()
    engine = PaperTradingEngine(
        account_id="ACC_SHORT_TEST",
        strategy_version_id="mock_short",
        symbol="PAXGUSDT",
        initial_capital=10000.0,
        strategy_interpreter=strategy,
    )

    # Bar 1: Short signal
    bar1 = {"timestamp": "2026-03-15T12:00:00Z", "open": 2710.0, "high": 2715.0, "low": 2698.0, "close": 2700.0, "spread": 0.20}
    await engine.on_candle_closed(bar1)
    assert engine.pending_order is not None
    assert engine.pending_order.direction == "SHORT"

    # Bar 2: Fills short at Open (2700.0 - 0.10 - 0.10 = 2699.80)
    bar2 = {"timestamp": "2026-03-15T12:05:00Z", "open": 2700.0, "high": 2705.0, "low": 2690.0, "close": 2695.0, "spread": 0.20}
    await engine.on_candle_closed(bar2)
    assert engine.position is not None
    assert engine.position.direction == "SHORT"

    # Bar 3: Hits Take Profit at low 2665 <= 2670
    bar3 = {"timestamp": "2026-03-15T12:10:00Z", "open": 2695.0, "high": 2698.0, "low": 2665.0, "close": 2668.0, "spread": 0.20}
    await engine.on_candle_closed(bar3)
    assert engine.position is None
    assert len(engine.trades_history) == 1
    assert engine.trades_history[0]["exit_reason"] == "TAKE_PROFIT"

