"""Unit tests for domain events and data models."""

from datetime import datetime, timezone
import pytest
from src.core.constants import OrderDirection, OrderType, MarketRegimeType
from src.core.events import (
    BarEvent,
    SignalEvent,
    RiskCheckResult,
    OrderEvent,
    FillEvent,
    SystemEvent,
)


def test_domain_events_instantiation_and_immutability():
    dt = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)

    # 1. BarEvent
    bar = BarEvent(
        instrument="XAUUSD",
        timeframe="1h",
        timestamp=dt,
        open=2060.0,
        high=2065.0,
        low=2058.0,
        close=2062.0,
        volume=100.0,
    )
    assert bar.instrument == "XAUUSD"
    with pytest.raises(Exception):
        bar.close = 2070.0  # frozen dataclass

    # 2. SignalEvent
    sig = SignalEvent(
        strategy_id="gold_ema_trend_cross",
        version_tag="v1",
        instrument="XAUUSD",
        timestamp=dt,
        direction=OrderDirection.LONG,
        suggested_entry=2062.0,
        suggested_sl=2050.0,
        suggested_tp=2086.0,
        suggested_size=1.0,
        risk_reward_ratio=2.0,
        regime=MarketRegimeType.TRENDING_UP,
    )
    assert sig.event_id is not None
    assert sig.direction == OrderDirection.LONG

    # 3. RiskCheckResult
    risk = RiskCheckResult(
        passed=True,
        reason="Approved",
        approved_size=1.0,
        approved_sl=2050.0,
        approved_tp=2086.0,
    )
    assert risk.passed is True

    # 4. OrderEvent
    order = OrderEvent(
        signal_id=sig.event_id,
        strategy_id=sig.strategy_id,
        instrument="XAUUSD",
        direction=OrderDirection.LONG,
        order_type=OrderType.MARKET,
        price=2062.0,
        size=1.0,
        stop_loss=2050.0,
        take_profit=2086.0,
    )
    assert order.order_id is not None

    # 5. FillEvent
    fill = FillEvent(
        order_id=order.order_id,
        signal_id=sig.event_id,
        instrument="XAUUSD",
        direction=OrderDirection.LONG,
        fill_price=2062.2,
        fill_size=1.0,
        slippage=0.2,
        fee=1.03,
    )
    assert fill.fill_id is not None
    assert fill.slippage == 0.2

    # 6. SystemEvent
    sys_event = SystemEvent(
        component="DATA_FEED",
        event_name="CONNECTION_ESTABLISHED",
        status="OK",
    )
    assert sys_event.status == "OK"

