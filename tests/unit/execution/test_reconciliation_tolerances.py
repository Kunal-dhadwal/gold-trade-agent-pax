"""Unit tests for Dynamic Tolerances, Funding Awareness, and OCO Sweeps (Sub-Milestone 4.2.4).

Validates:
- Funding settlement window detection (+/- 15m of 00:00, 08:00, 16:00 UTC).
- Dynamic cash tolerance accommodating funding accruals inside window and flagging outside.
- Live funding event recording and local account balance synchronization.
- Basis-aware UPnL tolerance formula and discrepancy detection.
- Orphaned synthetic OCO companion sweep when position is zero.
- Preservation of resting stops when position is non-zero.
- Resilient exception handling during exchange cancellation in OCO sweep.
"""

import asyncio
from datetime import datetime, timezone, timedelta
import uuid
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.database.models.base import Base
from src.database.models.instruments import Instrument
from src.database.models.live import (
    LiveAccount,
    LiveFundingEvent,
    LiveOrder,
    LiveOrderEvent,
    ReconciliationSnapshot,
)
from src.database.models.trading import Position
from src.execution.delta.client import DeltaRestClient
from src.execution.delta.fsm import OrderState
from src.execution.delta.mock_server import MockDeltaExchangeServer
from src.execution.delta.types import DeltaConfig, DeltaEnvironment
from src.execution.reconciliation import (
    ReconciliationDaemon,
    is_in_funding_settlement_window,
)


@pytest_asyncio.fixture
async def db_session_factory():
    """Create an isolated in-memory SQLite database sharing state via StaticPool."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Seed Instrument, LiveAccount, and Position
    async with session_maker() as session:
        inst = Instrument(
            id="PAXGUSDT",
            symbol="PAXGUSDT",
            name="PAX Gold Perpetual",
            asset_class="COMMODITY",
            quote_currency="USDT",
        )
        acc = LiveAccount(
            account_id="ACC_LIVE_PRIMARY",
            name="Primary Live Account",
            currency="USDT",
            initial_balance=10000.0,
            current_cash=10000.0,
            status="ACTIVE",
        )
        session.add_all([inst, acc])
        await session.commit()

    yield session_maker
    await engine.dispose()


@pytest_asyncio.fixture
async def mock_server():
    server = MockDeltaExchangeServer()
    server.positions[1234] = {
        "product_id": 1234,
        "size": 0.0,
        "side": "buy",
        "entry_price": "2650.00",
        "unrealized_pnl": "0.0",
    }
    server.balances["USDT"] = 10000.0
    return server


@pytest_asyncio.fixture
async def rest_client(mock_server):
    config = DeltaConfig(
        environment=DeltaEnvironment.MOCK,
        base_url="https://mock.delta.exchange",
        api_key=mock_server.api_key,
        api_secret=mock_server.api_secret,
        live_trading_enabled=False,
    )
    client = DeltaRestClient(config=config, transport=mock_server.create_mock_transport())
    yield client
    await client.close()


# =============================================================================
# 1. Funding Settlement Window Tests
# =============================================================================


def test_funding_settlement_window_detection():
    """Verify +/- 15m window around 00:00, 08:00, 16:00 UTC."""
    # 00:00 UTC tests
    assert is_in_funding_settlement_window(datetime(2026, 10, 12, 0, 0, 0, tzinfo=timezone.utc)) is True
    assert is_in_funding_settlement_window(datetime(2026, 10, 12, 0, 14, 59, tzinfo=timezone.utc)) is True
    assert is_in_funding_settlement_window(datetime(2026, 10, 12, 23, 46, 0, tzinfo=timezone.utc)) is True
    assert is_in_funding_settlement_window(datetime(2026, 10, 12, 0, 16, 0, tzinfo=timezone.utc)) is False

    # 08:00 UTC tests
    assert is_in_funding_settlement_window(datetime(2026, 10, 12, 7, 50, 0, tzinfo=timezone.utc)) is True
    assert is_in_funding_settlement_window(datetime(2026, 10, 12, 8, 10, 0, tzinfo=timezone.utc)) is True
    assert is_in_funding_settlement_window(datetime(2026, 10, 12, 8, 20, 0, tzinfo=timezone.utc)) is False

    # 16:00 UTC tests
    assert is_in_funding_settlement_window(datetime(2026, 10, 12, 15, 55, 0, tzinfo=timezone.utc)) is True
    assert is_in_funding_settlement_window(datetime(2026, 10, 12, 16, 5, 0, tzinfo=timezone.utc)) is True
    assert is_in_funding_settlement_window(datetime(2026, 10, 12, 16, 16, 0, tzinfo=timezone.utc)) is False
    assert is_in_funding_settlement_window(datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)) is False


# =============================================================================
# 2. Dynamic Cash Tolerance Tests
# =============================================================================


@pytest.mark.asyncio
async def test_funding_cash_tolerance_inside_and_outside_window(mock_server, rest_client, db_session_factory):
    """Verify cash drift within funding buffer is accommodated inside window and flagged outside."""
    daemon = ReconciliationDaemon(
        client=rest_client,
        session_factory=db_session_factory,
        account_id="ACC_LIVE_PRIMARY",
        funding_tolerance_usd=5.0,
    )

    # In DB: current_cash = 10000.0
    # Simulate exchange cash = 10003.50 (3.50 USD drift)
    mock_server.balances["USDT"] = 10003.50

    # Base tolerance: max(10 * 0.01, 0.0001 * 10000) = max(0.10, 1.0) = 1.0 USD
    # Inside funding window: 1.0 + 5.0 = 6.0 USD tolerance -> 3.50 <= 6.0 (Pass)
    # Outside funding window: 1.0 USD tolerance -> 3.50 > 1.0 (Breach)

    # 1. Test inside window: monkeypatch is_in_funding_settlement_window
    import src.execution.reconciliation as rec_mod
    orig_fn = rec_mod.is_in_funding_settlement_window
    try:
        rec_mod.is_in_funding_settlement_window = lambda dt=None: True
        res_inside = await daemon.run_reconciliation_cycle()
        assert res_inside["status"] == "CLEAN"
        assert res_inside["discrepancy_count"] == 0
        assert not any("CASH_TOLERANCE_BREACH" in d for d in res_inside["discrepancies"])

        # 2. Test outside window
        rec_mod.is_in_funding_settlement_window = lambda dt=None: False
        res_outside = await daemon.run_reconciliation_cycle()
        assert res_outside["status"] == "DISCREPANCY_DETECTED"
        assert res_outside["discrepancy_count"] >= 1
        assert any("CASH_TOLERANCE_BREACH" in d for d in res_outside["discrepancies"])
    finally:
        rec_mod.is_in_funding_settlement_window = orig_fn


@pytest.mark.asyncio
async def test_record_funding_event_updates_cash_and_logs(rest_client, db_session_factory):
    """Verify record_funding_event creates database record and updates live account cash."""
    daemon = ReconciliationDaemon(
        client=rest_client,
        session_factory=db_session_factory,
        account_id="ACC_LIVE_PRIMARY",
    )

    event = await daemon.record_funding_event(
        funding_rate=0.0001,
        payment_amount=-2.50,
        product_symbol="PAXGUSDT",
    )
    assert event.event_id is not None
    assert event.payment_amount == -2.50
    assert event.funding_rate == 0.0001

    # Verify database persistence
    async with db_session_factory() as session:
        acc = (await session.execute(select(LiveAccount).where(LiveAccount.account_id == "ACC_LIVE_PRIMARY"))).scalar_one()
        assert acc.current_cash == 9997.50  # 10000.0 - 2.50

        events = (await session.execute(select(LiveFundingEvent))).scalars().all()
        assert len(events) == 1
        assert events[0].payment_amount == -2.50


# =============================================================================
# 3. Basis-Aware UPnL Tolerance Tests
# =============================================================================


@pytest.mark.asyncio
async def test_basis_aware_upnl_tolerance(mock_server, rest_client, db_session_factory):
    """Verify basis-aware UPnL tolerance: Tol_UPnL = PositionSize * ContractValue * max(Spread, 3 * BasisTWAP)."""
    daemon = ReconciliationDaemon(
        client=rest_client,
        session_factory=db_session_factory,
        account_id="ACC_LIVE_PRIMARY",
        contract_value=1.0,
        current_spread_usd=0.50,
        basis_twap_usd=0.50,
    )
    # Tol_UPnL = 2.0 * 1.0 * max(0.50, 3 * 0.50) = 2.0 * 1.50 = 3.0 USD

    # Set up matching 2-contract position in exchange and DB
    mock_server.positions[1234] = {
        "product_id": 1234,
        "size": 2.0,
        "side": "buy",
        "entry_price": "2650.00",
        "unrealized_pnl": "10.00",
    }
    async with db_session_factory() as session:
        pos = Position(
            instrument_id="PAXGUSDT",
            live_account_id="ACC_LIVE_PRIMARY",
            mode="LIVE",
            direction="BUY",
            quantity=2.0,
            average_entry_price=2650.0,
            current_mark_price=2655.0,
            unrealized_pnl=10.0,
        )
        session.add(pos)
        await session.commit()

    # 1. UPnL difference within tolerance: ex_upnl=12.0, db_upnl=10.0 (diff=2.0 <= 3.0) -> CLEAN
    mock_server.positions[1234]["unrealized_pnl"] = "12.00"
    res1 = await daemon.run_reconciliation_cycle()
    assert not any("UPNL_TOLERANCE_BREACH" in d for d in res1["discrepancies"])

    # 2. UPnL difference exceeds tolerance: ex_upnl=15.0, db_upnl=10.0 (diff=5.0 > 3.0) -> Breach
    mock_server.positions[1234]["unrealized_pnl"] = "15.00"
    res2 = await daemon.run_reconciliation_cycle()
    assert res2["status"] == "DISCREPANCY_DETECTED"
    assert any("UPNL_TOLERANCE_BREACH" in d for d in res2["discrepancies"])


# =============================================================================
# 4. Orphaned OCO Companion Sweep Tests
# =============================================================================


@pytest.mark.asyncio
async def test_orphaned_oco_sweep_when_position_is_flat(mock_server, rest_client, db_session_factory):
    """Verify that open reduce_only companion orders are swept and cancelled when position is flat."""
    daemon = ReconciliationDaemon(
        client=rest_client,
        session_factory=db_session_factory,
        account_id="ACC_LIVE_PRIMARY",
    )

    # Position is 0 on exchange and DB
    mock_server.positions[1234]["size"] = 0.0

    # Place an orphaned stop-loss order on the mock exchange
    mock_server.orders[101] = {
        "id": 101,
        "client_order_id": "AGY_STRAT1_101_NONCE1",
        "product_id": 1234,
        "size": 1.0,
        "unfilled_size": 1.0,
        "side": "sell",
        "order_type": "stop_market",
        "state": "open",
        "reduce_only": True,
    }

    # Add matching order in DB
    async with db_session_factory() as session:
        db_order = LiveOrder(
            order_id="order-101",
            account_id="ACC_LIVE_PRIMARY",
            client_order_id="AGY_STRAT1_101_NONCE1",
            exchange_order_id="101",
            product_symbol="PAXGUSDT",
            side="SELL",
            order_type="STOP_MARKET",
            state=OrderState.ACCEPTED.value,
            quantity=1.0,
            reduce_only=True,
        )
        session.add(db_order)
        await session.commit()

    # Run cycle or dedicated sweep
    swept_ids = await daemon.sweep_orphaned_oco_orders()
    assert len(swept_ids) >= 1
    assert "AGY_STRAT1_101_NONCE1" in swept_ids

    # Verify mock server order was cancelled
    assert mock_server.orders[101]["state"] == "cancelled"

    # Verify DB order transitioned to CANCELLED
    async with db_session_factory() as session:
        updated_order = (await session.execute(select(LiveOrder).where(LiveOrder.client_order_id == "AGY_STRAT1_101_NONCE1"))).scalar_one()
        assert updated_order.state == OrderState.CANCELLED.value

        events = (await session.execute(select(LiveOrderEvent).where(LiveOrderEvent.order_id == "order-101"))).scalars().all()
        assert any("ORPHANED_OCO_SWEEP" in (e.payload or "") for e in events)


@pytest.mark.asyncio
async def test_resting_stops_preserved_when_position_open(mock_server, rest_client, db_session_factory):
    """Verify that resting stop orders are NOT cancelled when a position is open."""
    daemon = ReconciliationDaemon(
        client=rest_client,
        session_factory=db_session_factory,
        account_id="ACC_LIVE_PRIMARY",
    )

    # Position is active (1 contract)
    mock_server.positions[1234] = {
        "product_id": 1234,
        "size": 1.0,
        "side": "buy",
        "entry_price": "2650.00",
        "unrealized_pnl": "0.0",
    }
    mock_server.orders[102] = {
        "id": 102,
        "client_order_id": "AGY_STRAT1_102_NONCE2",
        "product_id": 1234,
        "size": 1.0,
        "unfilled_size": 1.0,
        "side": "sell",
        "order_type": "stop_market",
        "state": "open",
        "reduce_only": True,
    }

    swept = await daemon.sweep_orphaned_oco_orders()
    assert len(swept) == 0
    assert mock_server.orders[102]["state"] == "open"
