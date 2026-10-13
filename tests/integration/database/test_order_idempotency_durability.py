"""Integration tests for durable order intent, cross-process idempotency, FSM persistence, and restart recovery.

Validates Sub-Milestone 4.2.2 requirements:
- Transactional persistence of order intent in PostgreSQL/DB before exchange dispatch.
- Cross-process idempotency enforcement via database uniqueness constraints.
- Detection and rejection of conflicting parameters under identical client_order_id.
- Concurrent multi-instance submission handling without duplicate execution.
- Restart recovery of active order states, active exit sets, and bracket pairings.
- Durable FSM transition audit history in live_order_events.
- Durable timeout escalation to RECONCILING in the database.
"""

import asyncio
from datetime import datetime, timezone
import os
import uuid
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.database.models.base import Base
from src.database.models.live import LiveAccount, LiveOrder, LiveOrderEvent
from src.execution.delta.client import DeltaRestClient
from src.execution.delta.fsm import OrderState
from src.execution.delta.idempotency import ClientOrderIdGenerator, IdempotencyManager
from src.execution.delta.mock_server import MockDeltaExchangeServer
from src.execution.delta.order_router import LiveOrderRouter
from src.execution.delta.types import (
    DeltaConfig,
    DeltaEnvironment,
    DeltaNetworkError,
    DeltaOrderRequest,
    DeltaOrderSide,
    DeltaOrderType,
    IdempotencyConflictError,
    InterPositionEntryBlockedError,
)
from tests.integration.database.test_postgresql_runtime_compatibility import (
    check_postgresql_reachability,
    get_verified_test_urls,
    validate_test_database_target,
)


from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db_session_factory():
    """Create an isolated database engine and session factory.
    
    Uses PostgreSQL 'gold_trading_test' if reachable; otherwise uses an in-memory SQLite
    async database with StaticPool so all sessions share the in-memory database.
    """
    pg_reachable, _ = check_postgresql_reachability()
    test_db_path = None
    if pg_reachable:
        async_url, _ = get_verified_test_urls()
        validate_test_database_target(async_url)
        engine = create_async_engine(async_url, echo=False)
    else:
        test_db_path = f"./data/test_idempotency_{uuid.uuid4().hex[:8]}.db"
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{test_db_path}",
            connect_args={"timeout": 30.0},
            echo=False,
        )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Seed default test account
    async with session_maker() as session:
        acc = LiveAccount(
            account_id="ACC_LIVE_PRIMARY",
            name="Primary Test Live Account",
            currency="USDT",
            initial_balance=10000.0,
            current_cash=10000.0,
            status="ACTIVE",
        )
        session.add(acc)
        try:
            await session.commit()
        except Exception:
            await session.rollback()

    yield session_maker

    # Teardown
    async with engine.begin() as conn:
        if not pg_reachable:
            await conn.run_sync(Base.metadata.drop_all)
        else:
            # Clean up test rows in PostgreSQL without dropping schema
            await conn.execute(text("DELETE FROM live_order_events;"))
            await conn.execute(text("DELETE FROM live_orders;"))
            await conn.execute(text("DELETE FROM live_accounts WHERE account_id = 'ACC_LIVE_PRIMARY';"))
    await engine.dispose()
    if test_db_path is not None and os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except OSError:
            pass


@pytest.fixture
def mock_delta_client():
    """Create mock exchange server and client."""
    server = MockDeltaExchangeServer(enforce_signatures=True)
    transport = server.create_mock_transport()
    config = DeltaConfig(
        environment=DeltaEnvironment.MOCK,
        base_url="https://mock.delta.exchange",
        api_key=server.api_key,
        api_secret=server.api_secret,
        live_trading_enabled=False,
    )
    client = DeltaRestClient(config=config, transport=transport)
    return server, client


@pytest.mark.asyncio
async def test_duplicate_submission_after_router_restart(db_session_factory, mock_delta_client):
    """Test 1: Duplicate submission after router process restart is recognized via database without re-dispatch."""
    server, client = mock_delta_client

    # Router Instance 1
    router1 = LiveOrderRouter(client=client, session_factory=db_session_factory)
    cid = ClientOrderIdGenerator.generate(strategy_id="TF01", bar_timestamp=1728640000)

    req = DeltaOrderRequest(
        product_id=1234,
        size=5,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2650.0,
        client_order_id=cid,
    )

    res1 = await router1.dispatch_order(req)
    assert res1["client_order_id"] == cid
    assert res1["state"] == OrderState.ACCEPTED.value
    assert len(server.orders) == 1

    # Simulate router crash and restart (fresh in-memory state, empty cache)
    router2 = LiveOrderRouter(client=client, session_factory=db_session_factory)
    assert cid not in router2._order_records
    assert cid not in router2.idempotency_manager._cache

    # Resubmit identical order to Router Instance 2
    res2 = await router2.dispatch_order(req)
    assert res2["client_order_id"] == cid
    # Must return existing order record from DB without placing duplicate on exchange
    assert len(server.orders) == 1
    assert str(res2["id"]) == str(res1["id"])


@pytest.mark.asyncio
async def test_conflicting_payload_after_restart_raises_conflict(db_session_factory, mock_delta_client):
    """Test 2: Conflicting order payload under identical client_order_id raises IdempotencyConflictError after restart."""
    _, client = mock_delta_client

    router1 = LiveOrderRouter(client=client, session_factory=db_session_factory)
    cid = ClientOrderIdGenerator.generate(strategy_id="TF02", bar_timestamp=1728640000)

    req1 = DeltaOrderRequest(
        product_id=1234,
        size=5,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2650.0,
        client_order_id=cid,
    )
    await router1.dispatch_order(req1)

    # Fresh Router Instance with empty cache
    router2 = LiveOrderRouter(client=client, session_factory=db_session_factory)

    # Submit with conflicting size (10 instead of 5)
    conflicting_req = DeltaOrderRequest(
        product_id=1234,
        size=10,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2650.0,
        client_order_id=cid,
    )

    with pytest.raises(IdempotencyConflictError) as exc_info:
        await router2.dispatch_order(conflicting_req)

    assert "parameter 'size' mismatch" in str(exc_info.value)


@pytest.mark.asyncio
async def test_two_router_instances_concurrent_submission(db_session_factory, mock_delta_client):
    """Test 3: Two separate router instances submitting identical client_order_id concurrently."""
    server, client = mock_delta_client

    routerA = LiveOrderRouter(client=client, session_factory=db_session_factory)
    routerB = LiveOrderRouter(client=client, session_factory=db_session_factory)
    cid = ClientOrderIdGenerator.generate(strategy_id="MULTI", bar_timestamp=1728640000)

    req = DeltaOrderRequest(
        product_id=1234,
        size=3,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2600.0,
        client_order_id=cid,
    )

    resA, resB = await asyncio.gather(
        routerA.dispatch_order(req),
        routerB.dispatch_order(req),
    )

    assert resA["client_order_id"] == cid
    assert resB["client_order_id"] == cid
    # Exactly one logical order created and placed on exchange matching engine
    assert len(server.orders) == 1

    # Verify single row in live_orders table
    async with db_session_factory() as session:
        stmt = select(LiveOrder).where(LiveOrder.client_order_id == cid)
        result = await session.execute(stmt)
        orders = result.scalars().all()
        assert len(orders) == 1


@pytest.mark.asyncio
async def test_restart_recovery_reconstructs_active_orders_and_brackets(db_session_factory, mock_delta_client):
    """Test 4: Post-restart recovery reconstructs active orders, active exits, and bracket pairings."""
    server, client = mock_delta_client
    server.positions[1234] = {"size": 5}

    router1 = LiveOrderRouter(client=client, session_factory=db_session_factory)

    entry_cid = "AGY_REC_ENT_1"
    sl_cid = "AGY_REC_SL_1"
    tp_cid = "AGY_REC_TP_1"

    # 1. Dispatch Entry order
    entry_req = DeltaOrderRequest(
        product_id=1234,
        size=5,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2650.0,
        client_order_id=entry_cid,
    )
    await router1.dispatch_order(entry_req)

    # 2. Dispatch Stop Loss exit order
    sl_req = DeltaOrderRequest(
        product_id=1234,
        size=5,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.STOP_MARKET,
        stop_price=2600.0,
        reduce_only=True,
        client_order_id=sl_cid,
    )
    await router1.dispatch_order(sl_req, is_protective=True)

    # 3. Scale brackets and link pairing in database
    await router1.handle_partial_fill(
        entry_client_order_id=entry_cid,
        filled_quantity=5.0,
        sl_client_order_id=sl_cid,
        tp_client_order_id=tp_cid,
    )

    # Router 1 has active scaled replacement exit in memory
    scaled_sl_cid = router1._bracket_pairs[entry_cid]["sl_id"]
    assert scaled_sl_cid in router1._active_exit_orders

    # Simulate restart: Router 2 is created fresh
    router2 = LiveOrderRouter(client=client, session_factory=db_session_factory)
    assert len(router2._fsm_registry) == 0
    assert len(router2._active_exit_orders) == 0
    assert len(router2._bracket_pairs) == 0

    # Execute recovery from database
    recovery_summary = await router2.recover_state()
    assert recovery_summary["active_orders"] >= 2
    assert recovery_summary["active_exits"] >= 1
    assert recovery_summary["bracket_pairs"] >= 1

    # Active exit order set is restored
    assert scaled_sl_cid in router2._active_exit_orders
    # Bracket pairing and companion maps are restored
    assert router2._bracket_pairs[entry_cid]["sl_id"] == scaled_sl_cid
    assert router2._companion_map[scaled_sl_cid] == tp_cid

    # Inter-position entry blocking is enforced from recovered state!
    new_entry_req = DeltaOrderRequest(
        product_id=1234,
        size=1,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2640.0,
        client_order_id="AGY_REC_NEW_1",
    )
    with pytest.raises(InterPositionEntryBlockedError):
        await router2.dispatch_order(new_entry_req, is_protective=False)


@pytest.mark.asyncio
async def test_durable_fsm_audit_events_persisted(db_session_factory, mock_delta_client):
    """Test 5: LiveOrderEvent transition history is durably recorded in PostgreSQL/DB."""
    _, client = mock_delta_client
    router = LiveOrderRouter(client=client, session_factory=db_session_factory)
    cid = "AGY_EVT_AUD_1"

    req = DeltaOrderRequest(
        product_id=1234,
        size=2,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2650.0,
        client_order_id=cid,
    )

    await router.dispatch_order(req)

    # Inspect live_order_events table in database
    async with db_session_factory() as session:
        stmt = (
            select(LiveOrderEvent)
            .join(LiveOrder, LiveOrderEvent.order_id == LiveOrder.order_id)
            .where(LiveOrder.client_order_id == cid)
            .order_by(LiveOrderEvent.timestamp.asc())
        )
        res = await session.execute(stmt)
        events = res.scalars().all()

        # Must record: REGISTER_INTENT, SIGN, SUBMIT, and ACK transitions
        event_types = [e.event_type for e in events]
        assert "REGISTER_INTENT" in event_types
        assert "TRANSITION_TO_SIGNED" in event_types
        assert "TRANSITION_TO_SUBMITTED" in event_types
        assert "TRANSITION_TO_ACCEPTED" in event_types

        # Ensure all events have valid timestamps and order foreign key
        for ev in events:
            assert ev.order_id is not None
            assert ev.timestamp is not None


@pytest.mark.asyncio
async def test_network_timeout_persists_reconciling_state_in_database(db_session_factory, mock_delta_client):
    """Test 6: Transport timeout durably records RECONCILING state and FSM escalation events in DB."""
    server, client = mock_delta_client
    server.force_network_timeout = True

    router = LiveOrderRouter(client=client, session_factory=db_session_factory, clock_drift_evaluator=lambda: 50.0)
    cid = "AGY_TO_DUR_1"

    req = DeltaOrderRequest(
        product_id=1234,
        size=1,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2600.0,
        client_order_id=cid,
    )

    with pytest.raises(DeltaNetworkError):
        await router.dispatch_order(req)

    # Inspect live_orders in database
    async with db_session_factory() as session:
        stmt = select(LiveOrder).where(LiveOrder.client_order_id == cid)
        res = await session.execute(stmt)
        db_order = res.scalar_one_or_none()

        assert db_order is not None
        assert db_order.state == OrderState.RECONCILING.value

        # Inspect events: must record transition to FAILED and ESCALATE_RECONCILING
        ev_stmt = (
            select(LiveOrderEvent)
            .where(LiveOrderEvent.order_id == db_order.order_id)
            .order_by(LiveOrderEvent.timestamp.asc())
        )
        ev_res = await session.execute(ev_stmt)
        events = ev_res.scalars().all()
        to_states = [e.to_state for e in events]

        assert "FAILED" in to_states
        assert "RECONCILING" in to_states
