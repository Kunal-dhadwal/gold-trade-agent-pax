"""Unit tests for Real-Time Infrastructure (EventBus, StateCache, Idempotency, MarketData)."""

import asyncio
from datetime import datetime, timezone
import pytest
from src.real_time.event_bus import EventBus, EventEnvelope
from src.real_time.idempotency import IdempotencyEngine
from src.real_time.market_data import SimulatedLiveMarketData
from src.real_time.recovery import StateRehydrationEngine
from src.real_time.state_cache import StateCache


@pytest.mark.asyncio
async def test_idempotency_engine_key_formats_and_dedup():
    engine = IdempotencyEngine()  # In-memory fallback mode
    sig_key = IdempotencyEngine.build_signal_key("strat_v1", "2026-03-15T12:00:00Z", "LONG")
    assert "strat_v1" in sig_key
    assert "LONG" in sig_key

    # First check should be false (not seen)
    assert await engine.is_duplicate(sig_key) is False

    # Second check should be true (duplicate)
    assert await engine.is_duplicate(sig_key) is True


@pytest.mark.asyncio
async def test_event_bus_pubsub_dispatch():
    bus = EventBus()  # In-memory local dispatch
    received_events = []

    async def sample_handler(envelope: EventEnvelope):
        received_events.append(envelope)

    bus.subscribe("orders.ACC_1", sample_handler)

    env = EventEnvelope(
        event_type="ORDER_FILLED",
        aggregate_id="ACC_1",
        idempotency_key="key_123",
        payload={"order_id": "O_1", "fill_price": 2715.0},
    )

    await bus.publish("orders.ACC_1", env)
    await asyncio.sleep(0.01)

    assert len(received_events) == 1
    assert received_events[0].payload["order_id"] == "O_1"


@pytest.mark.asyncio
async def test_state_cache_crud():
    cache = StateCache()  # In-memory fallback
    account_id = "ACC_TEST"

    await cache.set_account(account_id, {"balance": 10000.0, "equity": 10050.0})
    acc_data = await cache.get_account(account_id)
    assert acc_data is not None
    assert acc_data["equity"] == 10050.0

    await cache.set_position(account_id, "PAXGUSDT", {"symbol": "PAXGUSDT", "direction": "LONG", "qty": 10.0})
    pos_data = await cache.get_position(account_id, "PAXGUSDT")
    assert pos_data is not None
    assert pos_data["direction"] == "LONG"

    await cache.delete_position(account_id, "PAXGUSDT")
    assert await cache.get_position(account_id, "PAXGUSDT") is None


@pytest.mark.asyncio
async def test_simulated_market_data_replay():
    candles = [
        {"timestamp": "2026-03-15T12:00:00Z", "open": 2700.0, "high": 2710.0, "low": 2695.0, "close": 2705.0, "volume": 100},
        {"timestamp": "2026-03-15T12:05:00Z", "open": 2705.0, "high": 2715.0, "low": 2700.0, "close": 2712.0, "volume": 120},
    ]

    sim = SimulatedLiveMarketData(candles=candles, symbol="PAXGUSDT")
    received = []

    async def on_candle(c):
        received.append(c)

    sim.subscribe(on_candle)
    await sim.start()
    await sim.play_all()

    assert len(received) == 2
    assert received[0]["close"] == 2705.0
    assert received[1]["close"] == 2712.0

