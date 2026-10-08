"""Integration test for full PaperTradingEngine execution lifecycle."""

import asyncio
from datetime import datetime, timezone
import pytest
from src.paper.engine import PaperTradingEngine
from src.paper.matching import OrderState
from src.strategy.base import BaseStrategy, StrategySignal


class MockTrendStrategy(BaseStrategy):
    """Mock strategy triggering deterministic signals for testing."""

    strategy_id: str = "mock_trend"
    name: str = "Mock Trend Strategy"

    def __init__(self, should_signal: bool = True):
        super().__init__()
        self.should_signal = should_signal

    def define_parameters(self):
        return []


    def generate_signals(self, df):

        if not self.should_signal:
            return []
        return [
            {
                "timestamp": df.iloc[-1].get("timestamp", datetime.now(timezone.utc).isoformat()),
                "direction": 1,  # LONG
                "entry_price": 2700.0,
                "stop_loss": 2680.0,
                "take_profit": 2720.0,
                "size": 100.0,
            }
        ]


@pytest.mark.asyncio
async def test_paper_engine_full_lifecycle():
    strategy = MockTrendStrategy(should_signal=True)
    engine = PaperTradingEngine(
        account_id="ACC_LIFECYCLE_TEST",
        strategy_version_id="mock_trend",
        symbol="PAXGUSDT",
        initial_capital=10000.0,
        strategy_interpreter=strategy,
    )

    # BAR 1: Signal generated at bar close, queues pending order for Next-Bar Open
    bar1 = {
        "timestamp": "2026-03-15T12:00:00Z",
        "open": 2690.0,
        "high": 2702.0,
        "low": 2688.0,
        "close": 2700.0,
        "spread": 0.20,
    }
    await engine.on_candle_closed(bar1)

    assert engine.pending_order is not None
    assert engine.pending_order.state == OrderState.ACCEPTED
    assert engine.position is None

    # BAR 2: Fills pending order at Next-Bar Open (2702.0 + 0.10 + 0.15 = 2702.25)
    strategy.should_signal = False  # Don't generate additional signals
    bar2 = {
        "timestamp": "2026-03-15T12:05:00Z",
        "open": 2702.0,
        "high": 2715.0,
        "low": 2701.0,
        "close": 2710.0,
        "spread": 0.20,
    }
    await engine.on_candle_closed(bar2)

    assert engine.pending_order is None
    assert engine.position is not None
    assert engine.position.direction == "LONG"
    assert engine.position.entry_price == 2702.20
    assert engine.portfolio.margin_reserved > 0

    # BAR 3: High reaches 2725.0, hitting Take Profit at 2720.0
    bar3 = {
        "timestamp": "2026-03-15T12:10:00Z",
        "open": 2710.0,
        "high": 2725.0,
        "low": 2708.0,
        "close": 2722.0,
        "spread": 0.20,
    }
    await engine.on_candle_closed(bar3)

    # Position closed via bracket TP exit
    assert engine.position is None
    assert engine.portfolio.margin_reserved == 0.0
    assert len(engine.trades_history) == 1
    assert engine.trades_history[0]["exit_reason"] == "TAKE_PROFIT"
    assert engine.portfolio.cash_balance > 10000.0  # Profitable trade settled


@pytest.mark.asyncio
async def test_paper_engine_emergency_flatten():
    engine = PaperTradingEngine(
        account_id="ACC_FLATTEN_TEST",
        strategy_version_id="mock_trend",
        symbol="PAXGUSDT",
        initial_capital=10000.0,
    )

    # Seed an open position
    from src.paper.positions import PositionTracker
    engine.position = PositionTracker(
        symbol="PAXGUSDT",
        direction="LONG",
        quantity=50.0,
        entry_price=2700.0,
        stop_loss=2680.0,
        take_profit=2740.0,
        allocated_margin=135.0,
        opened_at=datetime.now(timezone.utc),
    )

    res = await engine.flatten_all(reason="Manual test flatten", triggered_by="TEST_OPERATOR")
    assert res["status"] == "FLATTENED"
    assert res["positions_flattened"] == 1
    assert engine.position is None
    assert engine.risk_engine.kill_switch.is_active() is True
