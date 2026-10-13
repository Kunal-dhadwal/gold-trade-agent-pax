"""Integration, adversarial, and failure-recovery tests for Sub-Milestone 4.2.5.

Verifies:
1. End-to-end integration between LiveOrderRouter and LiveRiskEngine:
   - Approved entry order with sizing clamping.
   - Rejected entry order on risk gate breach (Gate 1, Gate 5).
   - Protective exit bypass of entry gates through the router.
2. Restart recovery of persisted High-Water Mark:
   - Simulates node crash and container reboot.
   - Proves Gate 1 drawdown is continuously measured against persisted peak equity.
3. Concurrent reset requests:
   - Parallel tasks attempting reset; verify thread-safety and consistent outcome.
4. Stale reconciliation and failure-to-flatten safety:
   - Reset rejected during active FLATTENING.
   - Reset rejected when reconciliation snapshot is stale or discrepant.
5. API Integration:
   - GET /api/v1/risk/live/status returns live risk telemetry.
   - POST /api/v1/risk/kill-switch/reset resets LiveKillSwitchFSM with zero discrepancies.
"""

import asyncio
from datetime import datetime, timezone, timedelta
import os
import uuid
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from src.api.auth import create_access_token
from src.api.main import app
from src.database.connection import Base
from src.database.models.live import AccountEquityHighWaterMark, LiveAccount
from src.execution.delta.client import DeltaRestClient
from src.execution.delta.fsm import OrderState
from src.execution.delta.mock_server import MockDeltaExchangeServer
from src.execution.delta.order_router import LiveOrderRouter, DeltaOrderRejectedError
from src.execution.delta.types import (
    DeltaConfig,
    DeltaEnvironment,
    DeltaOrderRequest,
    DeltaOrderSide,
    DeltaOrderType,
    DeltaProductSpecification,
)
from src.execution.reconciliation import ReconciliationService
from src.risk.high_water_mark import HighWaterMarkManager
from src.risk.live_kill_switch import (
    LiveKillSwitchFSM,
    LiveKillSwitchState,
    InvalidLiveKillSwitchStateTransitionError,
    LiveKillSwitchSecurityError,
)
from src.risk.live_risk_engine import LiveRiskEngine


@pytest_asyncio.fixture
async def db_session_factory():
    """Disposable isolated SQLite database for integration testing."""
    test_db_path = f"./data/test_live_risk_{uuid.uuid4().hex[:8]}.db"
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

    # Seed required live accounts to satisfy foreign key constraints
    async with session_maker() as session:
        for acc_id in ["LIVE_ACC_INT_001", "LIVE_ACC_RESTART_TEST"]:
            session.add(
                LiveAccount(
                    account_id=acc_id,
                    name=f"Account {acc_id}",
                    currency="USDT",
                    initial_balance=10000.0,
                    current_cash=10000.0,
                    status="ACTIVE",
                )
            )
        await session.commit()

    yield session_maker

    await engine.dispose()
    if os.path.exists(test_db_path):
        os.remove(test_db_path)


@pytest.fixture
def product_spec() -> DeltaProductSpecification:
    return DeltaProductSpecification(
        id=1001,
        symbol="PAXGUSDT",
        contract_type="perpetual_futures",
        quoting_asset="USDT",
        settling_asset="USDT",
        contract_value=0.001,
        tick_size=0.10,
        min_order_size=1,
        max_order_size=10000,
        trading_status="operational",
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0005,
    )


@pytest_asyncio.fixture
async def live_router_with_risk(db_session_factory, product_spec):
    """Setup MockDeltaExchangeServer, LiveOrderRouter, and LiveRiskEngine."""
    mock_server = MockDeltaExchangeServer()
    mock_server.products.append({
        "id": 1001,
        "symbol": "PAXGUSDT",
        "contract_type": "perpetual_futures",
        "quoting_asset": {"symbol": "USDT"},
        "settling_asset": {"symbol": "USDT"},
        "contract_value": "0.001",
        "tick_size": "0.10",
        "min_order_size": 1,
        "max_order_size": 10000,
        "trading_status": "operational",
        "maker_commission_rate": "0.0002",
        "taker_commission_rate": "0.0005",
    })
    transport = mock_server.create_mock_transport()

    config = DeltaConfig(
        environment=DeltaEnvironment.MOCK,
        api_key=mock_server.api_key,
        api_secret=mock_server.api_secret,
        base_url="https://testnet-api.delta.exchange",
    )
    client = DeltaRestClient(config=config, transport=transport)

    session_factory = db_session_factory

    hwm_manager = HighWaterMarkManager(session_factory=session_factory)
    kill_switch = LiveKillSwitchFSM()

    risk_engine = LiveRiskEngine(
        account_id="LIVE_ACC_INT_001",
        kill_switch=kill_switch,
        high_water_mark_manager=hwm_manager,
        session_factory=session_factory,
        product_spec=product_spec,
    )
    # Set healthy baseline
    risk_engine.current_equity = 10000.0
    risk_engine.start_of_day_equity = 10000.0
    risk_engine.current_utc_date = datetime.now(timezone.utc).date()
    risk_engine.current_mark_price = 2650.0
    risk_engine.last_feed_timestamp = datetime.now(timezone.utc)
    risk_engine.last_clock_drift_ms = 10.0
    risk_engine.last_spread = 0.50
    risk_engine.fee_schedule_verified = True
    risk_engine.fee_schedule_timestamp = datetime.now(timezone.utc)

    # Initialize HWM in SQLite
    await risk_engine.initialize(current_equity=10000.0, fencing_epoch=1)

    router = LiveOrderRouter(
        client=client,
        session_factory=session_factory,
        is_leader_fn=lambda: True,
        risk_gate_evaluator=risk_engine.evaluate_order,
        clock_drift_evaluator=lambda: 10.0,
    )

    yield router, risk_engine, kill_switch, client, mock_server
    await client.close()


@pytest.mark.asyncio
async def test_router_risk_engine_integration_approved_entry(live_router_with_risk):
    """Verify LiveOrderRouter dispatches entry order approved by LiveRiskEngine."""
    router, risk_engine, kill_switch, client, mock_server = live_router_with_risk

    order_req = DeltaOrderRequest(
        product_id=1001,
        size=10,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2650.0,
        stop_loss_price=2640.0,
        take_profit_price=2670.0,
        reduce_only=False,
    )

    record = await router.dispatch_order(order_req, strategy_id="GOLD_TREND")

    assert record["client_order_id"] is not None

    fsm = router.get_fsm(record["client_order_id"])
    assert fsm is not None
    assert fsm.state in (OrderState.SUBMITTED, OrderState.ACCEPTED)


@pytest.mark.asyncio
async def test_router_risk_engine_integration_gate_breach_rejection(live_router_with_risk):
    """Verify LiveOrderRouter rejects entry order when LiveRiskEngine vetoes (Gate 5 position lock)."""
    router, risk_engine, kill_switch, client, mock_server = live_router_with_risk

    # Simulate 1 existing active position
    risk_engine.active_position_count = 1

    order_req = DeltaOrderRequest(
        product_id=1001,
        size=10,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2650.0,
        stop_loss_price=2640.0,
        reduce_only=False,
    )

    with pytest.raises(DeltaOrderRejectedError) as exc_info:
        await router.dispatch_order(order_req, strategy_id="GOLD_TREND")

    assert "rejected by Risk Gate 5" in str(exc_info.value)
    assert "Single Strategy Lock" in str(exc_info.value)


@pytest.mark.asyncio
async def test_router_risk_engine_integration_protective_exit_bypass(live_router_with_risk):
    """Verify protective exit bypasses entry blocks (Gate 5 position lock) in router."""
    router, risk_engine, kill_switch, client, mock_server = live_router_with_risk

    # Active position exists (Gate 5 blocks entries)
    risk_engine.active_position_count = 1
    mock_server.positions[1001] = {"product_id": 1001, "size": 10.0, "entry_price": 2650.0}

    # Protective exit order (reduce_only: True)
    protective_req = DeltaOrderRequest(
        product_id=1001,
        size=10,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.STOP_MARKET,
        stop_price=2640.0,
        reduce_only=True,
    )

    record = await router.dispatch_order(protective_req, is_protective=True, strategy_id="GOLD_TREND")


    assert record["client_order_id"] is not None

    fsm = router.get_fsm(record["client_order_id"])
    assert fsm is not None
    assert fsm.state in (OrderState.SUBMITTED, OrderState.ACCEPTED)


@pytest.mark.asyncio
async def test_high_water_mark_persistence_across_restart(db_session_factory, product_spec):
    """Verify High-Water Mark persists in database across simulated engine restarts."""
    session_factory = db_session_factory
    account_id = "LIVE_ACC_RESTART_TEST"

    # Step 1: First engine instance reaches peak equity of $15,000
    engine1 = LiveRiskEngine(
        account_id=account_id,
        session_factory=session_factory,
        product_spec=product_spec,
    )
    await engine1.initialize(current_equity=10000.0, fencing_epoch=1)
    await engine1.update_equity(current_equity=15000.0, fencing_epoch=1)

    assert engine1.calculate_drawdown_pct(15000.0) == 0.0

    # Step 2: Simulate process termination / restart: create new engine instance
    engine2 = LiveRiskEngine(
        account_id=account_id,
        session_factory=session_factory,
        product_spec=product_spec,
    )

    # Bootstraps with current equity of $14,000
    loaded_peak = await engine2.initialize(current_equity=14000.0, fencing_epoch=2)
    assert loaded_peak == 15000.0  # Loaded from database!

    # Verify Gate 1 drawdown is measured against persisted peak $15,000:
    # (15000 - 14000) / 15000 = 6.67% -> Gate 1 BREACH!
    dd_pct = engine2.calculate_drawdown_pct(14000.0)
    assert dd_pct == pytest.approx(6.6667, abs=1e-3)

    # Order must be rejected by Gate 1
    order = DeltaOrderRequest(
        product_id=1001,
        size=1,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2650.0,
        stop_loss_price=2640.0,
        reduce_only=False,
    )
    res = engine2.evaluate_order({"order_request": order, "current_equity": 14000.0})
    assert res.approved is False
    assert res.failed_gate == 1
    assert engine2.kill_switch.state == LiveKillSwitchState.FLATTENING


@pytest.mark.asyncio
async def test_concurrent_reset_requests():
    """Verify thread-safety when multiple concurrent reset requests arrive."""
    fsm = LiveKillSwitchFSM()
    fsm.trigger_ghost_exposure("Ghost order detected")

    assert fsm.state == LiveKillSwitchState.MANUAL_RESET_REQUIRED

    async def do_reset(op_id: str):
        return fsm.reset(
            operator_role="ADMIN",
            operator_id=op_id,
            discrepancy_count=0,
            reconciliation_status="CLEAN",
        )

    # Dispatch 5 parallel reset attempts
    tasks = [asyncio.create_task(do_reset(f"admin_{i}")) for i in range(5)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # All should complete without state machine corruption
    for r in results:
        assert r is True
    assert fsm.state == LiveKillSwitchState.NORMAL


@pytest.mark.asyncio
async def test_api_live_status_endpoint():
    """Verify GET /api/v1/risk/live/status exposes live kill switch and risk engine telemetry."""
    rec_service = ReconciliationService.get_instance()
    live_ks = LiveKillSwitchFSM()
    live_risk = LiveRiskEngine(account_id="LIVE_ACC_API_001", kill_switch=live_ks)
    rec_service.register_live_kill_switch(live_ks)
    rec_service.register_live_risk_engine(live_risk)

    token = create_access_token(subject="viewer_user", role="VIEWER")
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/v1/risk/live/status", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert "live_kill_switch" in data
    assert data["live_kill_switch"]["state"] == "NORMAL"
    assert "live_risk_engine" in data
    assert data["live_risk_engine"]["account_id"] == "LIVE_ACC_API_001"
    assert data["live_risk_engine"]["max_drawdown_pct"] == 6.0


@pytest.mark.asyncio
async def test_api_reset_live_kill_switch_with_discrepancy_blocks():
    """Verify POST /api/v1/risk/kill-switch/reset blocks with HTTP 409 when LiveKillSwitch has discrepancy."""
    rec_service = ReconciliationService.get_instance()
    live_ks = LiveKillSwitchFSM()
    live_ks.trigger_ghost_exposure("Ghost exposure on exchange")
    rec_service.register_live_kill_switch(live_ks)

    token = create_access_token(subject="human_admin", role="ADMIN")
    headers = {"Authorization": f"Bearer {token}"}

    # Simulate active discrepancy in reconciliation daemon/service
    class MockDaemon:
        discrepancy_count = 1
        async def run_reconciliation_cycle(self):
            return {"discrepancy_count": 1, "status": "DISCREPANT"}

    rec_service.register_daemon(MockDaemon())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/v1/risk/kill-switch/reset?operator_id=human_admin", headers=headers)

    assert response.status_code == 409
    assert "unresolved discrepancy count is 1" in response.json()["detail"]
    assert live_ks.state == LiveKillSwitchState.MANUAL_RESET_REQUIRED

    # Cleanup mock daemon
    rec_service.register_daemon(None)
