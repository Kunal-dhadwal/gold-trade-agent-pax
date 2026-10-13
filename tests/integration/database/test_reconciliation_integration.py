"""Integration tests for Three-Way Reconciliation Daemon and Distributed Leadership Leases (Sub-Milestone 4.2.3).

Validates:
- Multi-cycle snapshot persistence in reconciliation_snapshots table.
- Ghost position adoption and immutable audit log persistence in operator_reconciliation_audit_logs.
- LiveOrderRouter entry blocking (DiscrepancyBlockedError) driven by daemon discrepancy counts.
- Distributed leadership lease gating and fencing across multiple workers.
- End-to-end reconciliation lifecycle with partial fill detection and bracket stop-loss resizing.
"""

import asyncio
from datetime import datetime, timezone, timedelta
import json
import os
import uuid
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.database.models.base import Base
from src.database.models.instruments import Instrument
from src.database.models.live import (
    LiveAccount,
    LiveOrder,
    LiveOrderEvent,
    OperatorReconciliationAuditLog,
    ReconciliationSnapshot,
)
from src.database.models.trading import Position
from src.execution.delta.client import DeltaRestClient
from src.execution.delta.fsm import OrderState
from src.execution.delta.idempotency import ClientOrderIdGenerator
from src.execution.delta.mock_server import MockDeltaExchangeServer
from src.execution.delta.order_router import LiveOrderRouter
from src.execution.delta.types import (
    DeltaConfig,
    DeltaEnvironment,
    DeltaOrderRequest,
    DeltaOrderSide,
    DeltaOrderType,
    DeltaTimeInForce,
    DiscrepancyBlockedError,
    LeadershipLostError,
)
from src.execution.leadership import LeaderLeaseManager
from src.execution.reconciliation import ReconciliationDaemon
from tests.integration.database.test_postgresql_runtime_compatibility import (
    check_postgresql_reachability,
    get_verified_test_urls,
    validate_test_database_target,
)


@pytest_asyncio.fixture
async def db_session_factory():
    """Create an isolated database engine and session factory.
    
    Uses PostgreSQL 'gold_trading_test' if reachable; otherwise uses an in-memory SQLite database.
    """
    pg_reachable, _ = check_postgresql_reachability()
    test_db_path = None
    if pg_reachable:
        async_url, _ = get_verified_test_urls()
        validate_test_database_target(async_url)
        engine = create_async_engine(async_url, echo=False)
    else:
        test_db_path = f"./data/test_reconciliation_{uuid.uuid4().hex[:8]}.db"
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

    # Seed default test instrument and account
    async with session_maker() as session:
        inst = Instrument(
            id="PAXGUSDT",
            symbol="PAXGUSDT",
            name="PAX Gold / Tether",
            asset_class="COMMODITY",
            quote_currency="USDT",
        )
        session.add(inst)
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
            await conn.execute(text("DELETE FROM operator_reconciliation_audit_logs;"))
            await conn.execute(text("DELETE FROM reconciliation_snapshots;"))
            await conn.execute(text("DELETE FROM live_order_events;"))
            await conn.execute(text("DELETE FROM live_orders;"))
            await conn.execute(text("DELETE FROM leader_leases;"))
            await conn.execute(text("DELETE FROM positions WHERE live_account_id = 'ACC_LIVE_PRIMARY';"))
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
    server.balances = {"USDT": 10000.0}
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


# --- Integration Tests ---

@pytest.mark.asyncio
async def test_multi_cycle_snapshot_persistence(db_session_factory, mock_delta_client):
    """Test 1: Multi-cycle snapshot persistence across clean, discrepancy, and resolved states."""
    server, client = mock_delta_client
    router = LiveOrderRouter(client=client, session_factory=db_session_factory)

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        order_router=router,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
        product_symbol="PAXGUSDT",
    )

    # Cycle 1: Clean
    snap1 = await daemon.run_reconciliation_cycle()
    assert snap1["status"] == "CLEAN"
    assert snap1["discrepancy_count"] == 0

    # Cycle 2: Introduce unknown order on exchange
    server.orders[9001] = {
        "id": 9001,
        "product_id": 1234,
        "size": 1.0,
        "unfilled_size": 1.0,
        "side": "buy",
        "order_type": "limit_order",
        "state": "open",
        "client_order_id": "AGY_ROGUE_01",
        "reduce_only": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    snap2 = await daemon.run_reconciliation_cycle()
    assert snap2["status"] == "DISCREPANCY_DETECTED"
    assert snap2["discrepancy_count"] == 1

    # Cycle 3: Remove unknown order -> Clean
    del server.orders[9001]
    snap3 = await daemon.run_reconciliation_cycle()
    assert snap3["status"] == "CLEAN"
    assert snap3["discrepancy_count"] == 0

    # Verify all 3 snapshots are durably stored in the database
    async with db_session_factory() as session:
        res = await session.execute(
            select(ReconciliationSnapshot)
            .where(ReconciliationSnapshot.account_id == "ACC_LIVE_PRIMARY")
            .order_by(ReconciliationSnapshot.timestamp.asc())
        )
        snapshots = res.scalars().all()
        assert len(snapshots) == 3
        assert snapshots[0].status == "CLEAN"
        assert snapshots[1].status == "DISCREPANCY_DETECTED"
        assert snapshots[2].status == "CLEAN"
        assert snapshots[1].discrepancy_count == 1


@pytest.mark.asyncio
async def test_ghost_position_adoption_and_audit_log_persistence(db_session_factory, mock_delta_client):
    """Test 2: Ghost position detection, latching Kill Switch, and operator adoption with audit logging."""
    server, client = mock_delta_client
    router = LiveOrderRouter(client=client, session_factory=db_session_factory)

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        order_router=router,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
    )

    # Exchange has 3.0 position without DB justification
    server.positions[1234] = {"product_id": 1234, "size": 3.0, "unrealized_pnl": 75.0}

    # Step 1: Detect ghost position
    detect_res = await daemon.run_reconciliation_cycle()
    assert detect_res["status"] == "GHOST_POSITION_DETECTED"
    assert router.kill_switch_state == "MANUAL_RESET_REQUIRED"

    # Step 2: Operator adopts the position
    adopt_res = await daemon.adopt_ghost_position(
        operator_id="LEAD_QUANT_01",
        product_id=1234,
        adopted_quantity=3.0,
        realized_price=2680.0,
        notes="Telephone trade confirmed with Delta Desk",
    )
    assert adopt_res["status"] == "CLEAN"
    assert adopt_res["discrepancy_count"] == 0

    # Step 3: Verify persistent database state
    async with db_session_factory() as session:
        # Position row updated
        p_res = await session.execute(select(Position).where(Position.live_account_id == "ACC_LIVE_PRIMARY"))
        pos = p_res.scalar_one()
        assert pos.quantity == 3.0
        assert pos.average_entry_price == 2680.0
        assert pos.direction == "BUY"

        # Immutable audit log persisted
        a_res = await session.execute(select(OperatorReconciliationAuditLog))
        audits = a_res.scalars().all()
        assert len(audits) == 1
        log_entry = audits[0]
        assert log_entry.operator_id == "LEAD_QUANT_01"
        assert log_entry.action_type == "ADOPT_GHOST_POSITION"
        payload = json.loads(log_entry.parameters)
        assert payload["adopted_quantity"] == 3.0
        assert payload["notes"] == "Telephone trade confirmed with Delta Desk"


@pytest.mark.asyncio
async def test_router_entry_blocking_when_reconciliation_detects_discrepancy(db_session_factory, mock_delta_client):
    """Test 3: Router rejects new entry orders with DiscrepancyBlockedError when reconciliation discrepancy > 0."""
    server, client = mock_delta_client
    router = LiveOrderRouter(client=client, session_factory=db_session_factory)

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        order_router=router,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
    )

    # Exchange has unknown order
    server.orders[8001] = {
        "id": 8001,
        "product_id": 1234,
        "size": 1.0,
        "unfilled_size": 1.0,
        "side": "buy",
        "order_type": "limit_order",
        "state": "open",
        "client_order_id": "AGY_UNKNOWN_888",
        "reduce_only": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Daemon detects discrepancy and sets router.discrepancy_count = 1
    await daemon.run_reconciliation_cycle()
    assert router.discrepancy_count == 1

    # Attempt to dispatch new entry order -> Blocked by Gate 3
    cid = ClientOrderIdGenerator.generate(strategy_id="TF01", bar_timestamp=1728640000)
    entry_req = DeltaOrderRequest(
        product_id=1234,
        size=1,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2650.0,
        client_order_id=cid,
        reduce_only=False,
    )

    with pytest.raises(DiscrepancyBlockedError):
        await router.dispatch_order(entry_req)

    # Protective exit order (reduce_only=True) is allowed through
    cid_exit = ClientOrderIdGenerator.generate(strategy_id="TF01", bar_timestamp=1728640001)
    exit_req = DeltaOrderRequest(
        product_id=1234,
        size=1,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.MARKET,
        client_order_id=cid_exit,
        reduce_only=True,
    )
    # Give mock server an open position so reduce_only passes
    server.positions[1234] = {"product_id": 1234, "size": 1.0, "unrealized_pnl": 0.0}

    # Should dispatch successfully without DiscrepancyBlockedError
    res_exit = await router.dispatch_order(exit_req)
    assert res_exit.get("id") is not None


@pytest.mark.asyncio
async def test_distributed_leadership_fencing_router_and_daemon(db_session_factory, mock_delta_client):
    """Test 4: Distributed leadership fencing coordinates daemon execution and order dispatch across workers."""
    server, client = mock_delta_client

    lease_mgr_A = LeaderLeaseManager(session_factory=db_session_factory)
    lease_mgr_B = LeaderLeaseManager(session_factory=db_session_factory)

    # Worker A acquires lease
    assert await lease_mgr_A.acquire_lease(owner_id="worker-node-A", ttl_seconds=10.0) is True

    # Router A & Daemon A
    router_A = LiveOrderRouter(
        client=client,
        session_factory=db_session_factory,
        is_leader_fn=lambda: lease_mgr_A.is_leader_valid(owner_id="worker-node-A"),
    )
    daemon_A = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        lease_manager=lease_mgr_A,
        order_router=router_A,
    )

    # Router B & Daemon B
    router_B = LiveOrderRouter(
        client=client,
        session_factory=db_session_factory,
        is_leader_fn=lambda: lease_mgr_B.is_leader_valid(owner_id="worker-node-B"),
    )
    daemon_B = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        lease_manager=lease_mgr_B,
        order_router=router_B,
    )

    # Worker A can run reconciliation and dispatch orders
    summary_A = await daemon_A.run_reconciliation_cycle()
    assert summary_A["status"] == "CLEAN"

    cid_A = ClientOrderIdGenerator.generate(strategy_id="LEAD01", bar_timestamp=1728640000)
    req_A = DeltaOrderRequest(
        product_id=1234,
        size=1,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.MARKET,
        client_order_id=cid_A,
    )
    res_A = await router_A.dispatch_order(req_A)
    assert res_A.get("id") is not None

    # Worker B is blocked: daemon skips, router raises LeadershipLostError
    summary_B = await daemon_B.run_reconciliation_cycle()
    assert summary_B["status"] == "SKIPPED_NOT_LEADER"

    cid_B = ClientOrderIdGenerator.generate(strategy_id="LEAD02", bar_timestamp=1728640000)
    req_B = DeltaOrderRequest(
        product_id=1234,
        size=1,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.MARKET,
        client_order_id=cid_B,
    )
    with pytest.raises(LeadershipLostError):
        await router_B.dispatch_order(req_B)

    # Clean failover: Worker A resigns, Worker B acquires
    await lease_mgr_A.resign_lease(owner_id="worker-node-A")
    assert await lease_mgr_B.acquire_lease(owner_id="worker-node-B", ttl_seconds=10.0) is True

    # Now Worker B can run reconciliation
    summary_B_after = await daemon_B.run_reconciliation_cycle()
    assert summary_B_after["status"] == "CLEAN"


@pytest.mark.asyncio
async def test_e2e_reconciliation_lifecycle_with_partial_fill_and_sl_resizing(db_session_factory, mock_delta_client):
    """Test 5: Full E2E lifecycle: submission -> partial fill -> SL resizing -> reconciliation clean pass."""
    server, client = mock_delta_client
    router = LiveOrderRouter(client=client, session_factory=db_session_factory)
    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        order_router=router,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
    )

    # 1. Dispatch entry order with initial bracket (TP and SL for 4 contracts)
    cid_entry = ClientOrderIdGenerator.generate(strategy_id="E2E01", bar_timestamp=1728640000)
    cid_tp = ClientOrderIdGenerator.generate(strategy_id="E2E01", bar_timestamp=1728640000, nonce="TP0001")
    cid_sl = ClientOrderIdGenerator.generate(strategy_id="E2E01", bar_timestamp=1728640000, nonce="SL0001")

    entry_req = DeltaOrderRequest(
        product_id=1234,
        size=4,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2650.0,
        client_order_id=cid_entry,
    )

    tp_req = DeltaOrderRequest(
        product_id=1234,
        size=4,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2750.0,
        client_order_id=cid_tp,
        reduce_only=True,
    )

    sl_req = DeltaOrderRequest(
        product_id=1234,
        size=4,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.STOP_MARKET,
        stop_price=2600.0,
        client_order_id=cid_sl,
        reduce_only=True,
    )

    # 1. Dispatch entry order
    entry_res = await router.dispatch_order(entry_req)
    ex_order_id = entry_res.get("id")
    assert ex_order_id is not None

    # Dispatch initial protective stop-loss on exchange (4 contracts)
    server.positions[1234] = {"product_id": 1234, "size": 4, "unrealized_pnl": 0.0}
    sl_res = await router.dispatch_order(sl_req, is_protective=True)
    old_sl_id = sl_res["id"]
    assert server.orders[old_sl_id]["state"] == "open"
    assert server.orders[old_sl_id]["size"] == 4

    # Pair bracket on router
    router._bracket_pairs[cid_entry] = {"sl_id": cid_sl, "tp_id": cid_tp}

    # 2. Simulate partial fill on exchange (2 of 4 contracts filled)
    server.orders[ex_order_id]["unfilled_size"] = 2.0
    server.positions[1234] = {"product_id": 1234, "size": 2.0, "unrealized_pnl": 10.0}

    # 3. Reconciliation cycle detects partial fill and triggers dynamic bracket scaling
    cycle_res = await daemon.run_reconciliation_cycle()

    # Verify DB order state updated to PARTIALLY_FILLED with filled_quantity = 2.0
    async with db_session_factory() as session:
        res = await session.execute(select(LiveOrder).where(LiveOrder.client_order_id == cid_entry))
        db_order = res.scalar_one()
        assert db_order.state == OrderState.PARTIALLY_FILLED.value
        assert db_order.filled_quantity == 2.0

    # Old stop order on exchange cancelled via cancel/replace
    assert server.orders[old_sl_id]["state"] == "cancelled"

    # Replacement stop order placed on exchange matching engine for exactly 2 contracts
    active_sl_cid = router._bracket_pairs[cid_entry]["sl_id"]
    active_sl_rec = router._order_records[active_sl_cid]
    new_sl_id = active_sl_rec["id"]
    assert server.orders[new_sl_id]["state"] == "open"
    assert server.orders[new_sl_id]["size"] == 2
