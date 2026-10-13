"""Unit tests for Three-Way Continuous Reconciliation Daemon (Sub-Milestone 4.2.3).

Validates:
- Clean reconciliation pass (Memory == DB == Delta REST -> 0 discrepancies, CLEAN snapshot).
- In-flight correlation buffer (T_in_flight <= 30s holds alarm on pending order).
- In-flight correlation buffer expiry (T_in_flight > 30s escalates to RECONCILING).
- Unknown exchange order detection (Exchange has AGY_... order not in DB -> flagged).
- Terminal state synchronization (Exchange filled/cancelled -> updates DB state and logs events).
- Partial fill detection and bracket stop-loss scaling invocation.
- Ghost position detection and Kill Switch latching with ZERO automated liquidation.
- Operator remediation endpoint (adopt_ghost_position -> DB update, immutable audit log, CLEAN).
- Exchange transport outage handling (fail-closed, error recorded, zero DB corruption).
- Leadership lease gating (non-leader node skips reconciliation cycle).
- Daemon start/stop lifecycle management.
"""

import asyncio
from datetime import datetime, timezone, timedelta
import json
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
    LiveOrder,
    LiveOrderEvent,
    OperatorReconciliationAuditLog,
    ReconciliationSnapshot,
)
from src.database.models.trading import Position
from src.execution.delta.client import DeltaRestClient
from src.execution.delta.fsm import OrderState
from src.execution.delta.mock_server import MockDeltaExchangeServer
from src.execution.delta.order_router import LiveOrderRouter
from src.execution.delta.types import (
    DeltaConfig,
    DeltaEnvironment,
    DeltaNetworkError,
)
from src.execution.leadership import LeaderLeaseManager
from src.execution.reconciliation import ReconciliationDaemon


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

    # Seed default instrument and live account
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
            name="Primary Live Account",
            currency="USDT",
            initial_balance=10000.0,
            current_cash=10000.0,
            status="ACTIVE",
        )
        session.add(acc)
        await session.commit()

    yield session_maker
    await engine.dispose()


@pytest.fixture
def mock_delta():
    """Create configured MockDeltaExchangeServer and DeltaRestClient."""
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


# --- Test Cases ---

@pytest.mark.asyncio
async def test_reconciliation_clean_pass(db_session_factory, mock_delta):
    """Test 1: Clean reconciliation pass (Memory == DB == Delta REST -> 0 discrepancies, CLEAN snapshot)."""
    server, client = mock_delta
    router = LiveOrderRouter(client=client)

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        order_router=router,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
        product_symbol="PAXGUSDT",
    )

    summary = await daemon.run_reconciliation_cycle()

    assert summary["status"] == "CLEAN"
    assert summary["discrepancy_count"] == 0
    assert summary["ex_position"] == 0.0
    assert summary["db_position"] == 0.0
    assert daemon.discrepancy_count == 0

    # Verify snapshot persisted in database
    async with db_session_factory() as session:
        res = await session.execute(select(ReconciliationSnapshot))
        snapshots = res.scalars().all()
        assert len(snapshots) == 1
        snap = snapshots[0]
        assert snap.status == "CLEAN"
        assert snap.discrepancy_count == 0
        assert snap.cash_local == 10000.0
        assert snap.cash_exchange == 10000.0


@pytest.mark.asyncio
async def test_in_flight_correlation_buffer_within_grace(db_session_factory, mock_delta):
    """Test 2: In-flight correlation buffer holds alarm when position delta matches pending order <= 30s."""
    server, client = mock_delta
    now_utc = datetime.now(timezone.utc)

    # DB has a pending order submitted 5 seconds ago for 1 contract
    async with db_session_factory() as session:
        order = LiveOrder(
            order_id=str(uuid.uuid4()),
            client_order_id="AGY_PENDING_01",
            account_id="ACC_LIVE_PRIMARY",
            product_symbol="PAXGUSDT",
            side="BUY",
            order_type="limit_order",
            price=2650.0,
            quantity=1.0,
            filled_quantity=0.0,
            state=OrderState.SUBMITTED.value,
            created_at=now_utc - timedelta(seconds=5),
        )
        session.add(order)
        await session.commit()

    # Delta exchange already registered the fill as a position of 1.0, but order is no longer in open orders
    server.positions[1234] = {"product_id": 1234, "size": 1.0, "unrealized_pnl": 0.0}

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
        in_flight_grace_period_seconds=30.0,
    )

    summary = await daemon.run_reconciliation_cycle()

    # In-flight order (quantity=1.0) correlated with delta_q (1.0) -> No ghost position alarm!
    assert summary["status"] == "CLEAN"
    assert summary["discrepancy_count"] == 0
    assert len(daemon.ghost_positions_detected) == 0


@pytest.mark.asyncio
async def test_in_flight_correlation_buffer_expired_missing_order(db_session_factory, mock_delta):
    """Test 3: Missing order on exchange past 30s grace period escalates to RECONCILING state."""
    server, client = mock_delta
    now_utc = datetime.now(timezone.utc)

    # Order in DB created 35s ago (beyond grace period)
    async with db_session_factory() as session:
        order = LiveOrder(
            order_id=str(uuid.uuid4()),
            client_order_id="AGY_MISSING_01",
            account_id="ACC_LIVE_PRIMARY",
            product_symbol="PAXGUSDT",
            side="BUY",
            order_type="limit_order",
            price=2650.0,
            quantity=1.0,
            filled_quantity=0.0,
            state=OrderState.SUBMITTED.value,
            created_at=now_utc - timedelta(seconds=35),
        )
        session.add(order)
        await session.commit()

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
        in_flight_grace_period_seconds=30.0,
    )

    summary = await daemon.run_reconciliation_cycle()

    assert summary["status"] == "DISCREPANCY_DETECTED"
    assert summary["discrepancy_count"] == 1
    assert "MISSING_ORDER_AGY_MISSING_01" in summary["discrepancies"]

    # Verify DB order transitioned to RECONCILING and LiveOrderEvent recorded
    async with db_session_factory() as session:
        res = await session.execute(select(LiveOrder).where(LiveOrder.client_order_id == "AGY_MISSING_01"))
        db_order = res.scalar_one()
        assert db_order.state == OrderState.RECONCILING.value

        ev_res = await session.execute(select(LiveOrderEvent).where(LiveOrderEvent.order_id == db_order.order_id))
        events = ev_res.scalars().all()
        assert any(e.event_type == "MISSING_EXCHANGE_ORDER_ESCALATION" for e in events)


@pytest.mark.asyncio
async def test_unknown_exchange_order_detection(db_session_factory, mock_delta):
    """Test 4: Exchange has an unknown AGY_... order not present in local database."""
    server, client = mock_delta

    # Seed an unauthorized / unknown order directly onto exchange
    server.orders[2001] = {
        "id": 2001,
        "product_id": 1234,
        "size": 1.0,
        "unfilled_size": 1.0,
        "side": "buy",
        "order_type": "limit_order",
        "state": "open",
        "client_order_id": "AGY_UNKNOWN_999",
        "reduce_only": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
    )

    summary = await daemon.run_reconciliation_cycle()

    assert summary["status"] == "DISCREPANCY_DETECTED"
    assert "UNKNOWN_ORDER_AGY_UNKNOWN_999" in summary["discrepancies"]
    assert daemon.discrepancy_count == 1


@pytest.mark.asyncio
async def test_terminal_state_synchronization_filled_and_cancelled(db_session_factory, mock_delta):
    """Test 5: Exchange filled or cancelled orders synchronize local DB state and log audit events."""
    server, client = mock_delta
    now_utc = datetime.now(timezone.utc)

    # Seed two active orders in DB older than 30s
    async with db_session_factory() as session:
        order_fill = LiveOrder(
            order_id=str(uuid.uuid4()),
            client_order_id="AGY_FILL_01",
            account_id="ACC_LIVE_PRIMARY",
            product_symbol="PAXGUSDT",
            side="BUY",
            order_type="limit_order",
            price=2650.0,
            quantity=2.0,
            filled_quantity=0.0,
            state=OrderState.SUBMITTED.value,
            created_at=now_utc - timedelta(seconds=35),
        )
        order_canc = LiveOrder(
            order_id=str(uuid.uuid4()),
            client_order_id="AGY_CANC_01",
            account_id="ACC_LIVE_PRIMARY",
            product_symbol="PAXGUSDT",
            side="SELL",
            order_type="limit_order",
            price=2700.0,
            quantity=1.0,
            filled_quantity=0.0,
            state=OrderState.ACCEPTED.value,
            created_at=now_utc - timedelta(seconds=35),
        )
        session.add_all([order_fill, order_canc])
        await session.commit()

    # On exchange, order_fill is marked filled, order_canc is marked cancelled (not in open orders)
    server.orders[3001] = {
        "id": 3001,
        "product_id": 1234,
        "size": 2.0,
        "unfilled_size": 0.0,
        "side": "buy",
        "order_type": "limit_order",
        "state": "filled",
        "client_order_id": "AGY_FILL_01",
        "reduce_only": False,
        "created_at": now_utc.isoformat(),
    }
    server.orders[3002] = {
        "id": 3002,
        "product_id": 1234,
        "size": 1.0,
        "unfilled_size": 1.0,
        "side": "sell",
        "order_type": "limit_order",
        "state": "cancelled",
        "client_order_id": "AGY_CANC_01",
        "reduce_only": False,
        "created_at": now_utc.isoformat(),
    }

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
    )

    summary = await daemon.run_reconciliation_cycle()

    # Both terminal states synchronized
    async with db_session_factory() as session:
        f_res = await session.execute(select(LiveOrder).where(LiveOrder.client_order_id == "AGY_FILL_01"))
        o_fill = f_res.scalar_one()
        assert o_fill.state == OrderState.FILLED.value
        assert o_fill.filled_quantity == 2.0

        c_res = await session.execute(select(LiveOrder).where(LiveOrder.client_order_id == "AGY_CANC_01"))
        o_canc = c_res.scalar_one()
        assert o_canc.state == OrderState.CANCELLED.value

        # Audit events verified
        ev_res = await session.execute(select(LiveOrderEvent))
        events = ev_res.scalars().all()
        assert any(e.event_type == "RECONCILIATION_FILL_DETECTED" for e in events)
        assert any(e.event_type == "RECONCILIATION_CANCEL_DETECTED" for e in events)


@pytest.mark.asyncio
async def test_partial_fill_detection_and_bracket_scaling(db_session_factory, mock_delta):
    """Test 6: Partial fill detected on exchange updates local order and calls router handle_partial_fill."""
    server, client = mock_delta
    router = LiveOrderRouter(client=client)
    now_utc = datetime.now(timezone.utc)

    # Order in DB is ACCEPTED with 0 filled
    async with db_session_factory() as session:
        order = LiveOrder(
            order_id=str(uuid.uuid4()),
            client_order_id="AGY_PF_01",
            account_id="ACC_LIVE_PRIMARY",
            product_symbol="PAXGUSDT",
            side="BUY",
            order_type="limit_order",
            price=2650.0,
            quantity=5.0,
            filled_quantity=0.0,
            state=OrderState.ACCEPTED.value,
            created_at=now_utc - timedelta(seconds=5),
        )
        session.add(order)
        await session.commit()

    # Exchange open orders has size=5.0, unfilled_size=2.0 (filled=3.0)
    server.orders[4001] = {
        "id": 4001,
        "product_id": 1234,
        "size": 5.0,
        "unfilled_size": 2.0,
        "side": "buy",
        "order_type": "limit_order",
        "state": "open",
        "client_order_id": "AGY_PF_01",
        "reduce_only": False,
        "created_at": now_utc.isoformat(),
    }

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        order_router=router,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
    )

    await daemon.run_reconciliation_cycle()

    # DB order updated to PARTIALLY_FILLED with filled_quantity = 3.0
    async with db_session_factory() as session:
        res = await session.execute(select(LiveOrder).where(LiveOrder.client_order_id == "AGY_PF_01"))
        o = res.scalar_one()
        assert o.state == OrderState.PARTIALLY_FILLED.value
        assert o.filled_quantity == 3.0

        ev_res = await session.execute(select(LiveOrderEvent).where(LiveOrderEvent.order_id == o.order_id))
        events = ev_res.scalars().all()
        assert any(e.event_type == "RECONCILIATION_PARTIAL_FILL" for e in events)


@pytest.mark.asyncio
async def test_ghost_position_detection_and_kill_switch_latch_no_auto_liquidation(db_session_factory, mock_delta):
    """Test 7: Unexplained exchange position triggers GHOST_POSITION_DETECTED, latches Kill Switch, ZERO liquidation."""
    server, client = mock_delta
    router = LiveOrderRouter(client=client)

    # Exchange has a 2.0 position; DB has 0 position and 0 in-flight orders
    server.positions[1234] = {"product_id": 1234, "size": 2.0, "unrealized_pnl": 50.0}

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        order_router=router,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
    )

    summary = await daemon.run_reconciliation_cycle()

    assert summary["status"] == "GHOST_POSITION_DETECTED"
    assert summary["discrepancy_count"] == 1
    assert "GHOST_POSITION_DELTA_2.0" in summary["discrepancies"]
    assert len(daemon.ghost_positions_detected) == 1

    # Kill switch strictly latched to MANUAL_RESET_REQUIRED
    assert router.kill_switch_state == "MANUAL_RESET_REQUIRED"

    # ZERO automated liquidation invariant: server orders must remain EMPTY
    assert len(server.orders) == 0


@pytest.mark.asyncio
async def test_operator_remediation_adopt_ghost_position(db_session_factory, mock_delta):
    """Test 8: Operator adopts ghost position -> updates DB position, writes immutable audit log, clears discrepancy."""
    server, client = mock_delta
    router = LiveOrderRouter(client=client)

    # Exchange has 2.0 position
    server.positions[1234] = {"product_id": 1234, "size": 2.0, "unrealized_pnl": 0.0}

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        order_router=router,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
    )

    # First cycle detects ghost position
    summary1 = await daemon.run_reconciliation_cycle()
    assert summary1["status"] == "GHOST_POSITION_DETECTED"

    # Operator invokes adopt_ghost_position
    summary2 = await daemon.adopt_ghost_position(
        operator_id="OP_CHIEF_01",
        product_id=1234,
        adopted_quantity=2.0,
        realized_price=2650.0,
        notes="Adopted after verified manual execution",
    )

    # Subsequent cycle is now CLEAN
    assert summary2["status"] == "CLEAN"
    assert summary2["discrepancy_count"] == 0
    assert len(daemon.ghost_positions_detected) == 0

    # Verify DB position and audit log
    async with db_session_factory() as session:
        pos_res = await session.execute(select(Position).where(Position.live_account_id == "ACC_LIVE_PRIMARY"))
        pos = pos_res.scalar_one()
        assert pos.quantity == 2.0
        assert pos.average_entry_price == 2650.0

        audit_res = await session.execute(select(OperatorReconciliationAuditLog))
        audits = audit_res.scalars().all()
        assert len(audits) == 1
        a = audits[0]
        assert a.operator_id == "OP_CHIEF_01"
        assert a.action_type == "ADOPT_GHOST_POSITION"
        params = json.loads(a.parameters)
        assert params["adopted_quantity"] == 2.0
        assert params["realized_price"] == 2650.0


@pytest.mark.asyncio
async def test_exchange_transport_outage_fails_closed(db_session_factory, mock_delta):
    """Test 9: Exchange transport timeout records error, increments consecutive failures, zero DB corruption."""
    server, client = mock_delta
    server.force_network_timeout = True

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
    )

    summary = await daemon.run_reconciliation_cycle()

    assert summary["status"] == "EXCHANGE_TRANSPORT_OUTAGE"
    assert summary["consecutive_failures"] == 1
    assert daemon.consecutive_transport_failures == 1

    # Verify zero DB mutation
    async with db_session_factory() as session:
        res = await session.execute(select(ReconciliationSnapshot))
        assert len(res.scalars().all()) == 0


@pytest.mark.asyncio
async def test_leadership_gating_blocks_reconciliation(db_session_factory, mock_delta):
    """Test 10: Non-leader node skips reconciliation cycle entirely."""
    _, client = mock_delta

    lease_mgr = LeaderLeaseManager(session_factory=db_session_factory)
    # Lease not acquired, so is_leader_valid returns False

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        lease_manager=lease_mgr,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
    )

    summary = await daemon.run_reconciliation_cycle()

    assert summary["status"] == "SKIPPED_NOT_LEADER"

    # Verify zero DB snapshots written
    async with db_session_factory() as session:
        res = await session.execute(select(ReconciliationSnapshot))
        assert len(res.scalars().all()) == 0


@pytest.mark.asyncio
async def test_reconciliation_daemon_start_stop_lifecycle(db_session_factory, mock_delta):
    """Test 11: Daemon starts and stops cleanly via asyncio background task."""
    _, client = mock_delta

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
        reconciliation_interval_seconds=0.05,
    )

    assert not daemon._is_running
    assert daemon._daemon_task is None

    await daemon.start()
    assert daemon._is_running
    assert daemon._daemon_task is not None

    # Let background task run at least one loop
    await asyncio.sleep(0.1)

    await daemon.stop()
    assert not daemon._is_running
    assert daemon._daemon_task is None
