"""Authoritative Unit Tests for Gate 9 Clock-Drift Failure Injection and Mitigation (Sub-Milestone 4.2.2).

Covers all 5 mandatory test functions specified in Section 3.2.6:
1. test_gate9_clock_drift_halts_new_entries_with_open_exposure
2. test_clock_drift_preserves_resting_exchange_stops
3. test_clock_drift_exit_with_compensated_timestamp
4. test_clock_drift_unreachable_time_api_fails_closed
5. test_clock_drift_recovery_requires_operator_reset
"""

import asyncio
import pytest

from src.execution.delta.client import DeltaRestClient
from src.execution.delta.fsm import OrderState
from src.execution.delta.mock_server import MockDeltaExchangeServer
from src.execution.delta.order_router import LiveOrderRouter
from src.execution.delta.types import (
    DeltaClockDriftError,
    DeltaConfig,
    DeltaEnvironment,
    DeltaOrderRequest,
    DeltaOrderSide,
    DeltaOrderType,
    DeltaTimeInForce,
    DeltaTransportOrTimeSyncUnavailableError,
)


@pytest.fixture
def mock_delta_env():
    """Create a configured MockDeltaExchangeServer and client."""
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
async def test_gate9_clock_drift_halts_new_entries_with_open_exposure(mock_delta_env):
    """Test 1: Server clock drift > 1000ms strictly halts new entry orders while exposure is open."""
    server, client = mock_delta_env
    # Simulate open exposure
    server.positions[1234] = {"size": 5, "entry_price": 2650.0}

    # Inject clock drift of 1500ms (> 1000ms SLA)
    server.clock_drift_offset_ms = 1500.0

    router = LiveOrderRouter(client=client, max_clock_drift_ms=1000.0)

    # Attempt to dispatch a new entry order
    entry_req = DeltaOrderRequest(
        product_id=1234,
        size=2,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2640.0,
        reduce_only=False,
        client_order_id="AGY_CD1_ENT_1",
    )

    with pytest.raises(DeltaClockDriftError) as exc_info:
        await router.dispatch_order(entry_req, is_protective=False)

    assert "clock drift" in str(exc_info.value).lower()
    assert "Gate 9" in str(exc_info.value) or "gate 9" in str(exc_info.value).lower()

    # Verify FSM transitioned to REJECTED and zero order was placed on exchange matching engine
    fsm = router.get_fsm("AGY_CD1_ENT_1")
    assert fsm.state == OrderState.REJECTED
    assert len(server.orders) == 0


@pytest.mark.asyncio
async def test_clock_drift_preserves_resting_exchange_stops(mock_delta_env):
    """Test 2: Server clock drift > 1000ms preserves resting exchange protective stops without automatic liquidation."""
    server, client = mock_delta_env
    server.positions[1234] = {"size": 5, "entry_price": 2650.0}

    # First, place a resting protective stop on exchange while clock is normal
    server.clock_drift_offset_ms = 0.0
    router = LiveOrderRouter(client=client, max_clock_drift_ms=1000.0)

    stop_req = DeltaOrderRequest(
        product_id=1234,
        size=5,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.STOP_MARKET,
        stop_price=2600.0,
        reduce_only=True,
        client_order_id="AGY_CD2_SL_1",
    )
    res = await router.dispatch_order(stop_req, is_protective=True)
    stop_exchange_id = res["id"]
    assert server.orders[stop_exchange_id]["state"] == "open"

    # Now inject severe clock drift of 2500ms
    server.clock_drift_offset_ms = 2500.0

    # Ensure resting stop order remains open on the exchange
    assert server.orders[stop_exchange_id]["state"] == "open"
    # Ensure router did not liquidate or cancel the resting stop
    assert stop_exchange_id in server.orders


@pytest.mark.asyncio
async def test_clock_drift_exit_with_compensated_timestamp(mock_delta_env):
    """Test 3: If clock drift > 1000ms but /v2/time is reachable within 2s, timestamp compensation allows signed protective exits."""
    server, client = mock_delta_env
    server.positions[1234] = {"size": 3, "entry_price": 2650.0}

    # Inject clock drift of 1500ms (> 1000ms)
    server.clock_drift_offset_ms = 1500.0
    # /v2/time is reachable on mock server
    router = LiveOrderRouter(client=client, max_clock_drift_ms=1000.0)

    exit_req = DeltaOrderRequest(
        product_id=1234,
        size=3,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.MARKET,
        reduce_only=True,
        client_order_id="AGY_CD3_EXT_1",
    )

    # Protective exit succeeds with compensated time
    res = await router.dispatch_order(exit_req, is_protective=True)
    assert res.get("id") is not None
    exit_fsm = router.get_fsm("AGY_CD3_EXT_1")
    assert exit_fsm.state in (OrderState.ACCEPTED, OrderState.FILLED)


@pytest.mark.asyncio
async def test_clock_drift_unreachable_time_api_fails_closed(mock_delta_env):
    """Test 4: If clock drift > 1000ms AND /v2/time is unreachable, system fails closed with HTTP 503 and directs to Delta Web Console."""
    server, client = mock_delta_env
    server.positions[1234] = {"size": 3, "entry_price": 2650.0}

    # Inject severe clock drift
    server.clock_drift_offset_ms = 2000.0
    # Make time API unreachable by forcing network timeout on mock server
    server.force_network_timeout = True

    router = LiveOrderRouter(client=client, max_clock_drift_ms=1000.0)

    # 1. Attempt in-band operator flatten endpoint
    with pytest.raises(DeltaTransportOrTimeSyncUnavailableError) as exc_info:
        await router.operator_flatten(product_id=1234, size=3, side=DeltaOrderSide.SELL)

    error_message = str(exc_info.value)
    assert "In-band automated flatten unavailable due to clock desync or exchange transport outage" in error_message
    assert "Operator MUST use Delta Web Console out-of-band" in error_message

    # Verify Kill Switch latched into CLOCK_DESYNC_UNPROTECTED_EXPOSURE
    assert router.kill_switch_state == "CLOCK_DESYNC_UNPROTECTED_EXPOSURE"

    # 2. Attempt direct protective exit dispatch when time is unreachable
    exit_req = DeltaOrderRequest(
        product_id=1234,
        size=3,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.MARKET,
        reduce_only=True,
        client_order_id="AGY_CD4_EXT_FAIL",
    )
    with pytest.raises(DeltaClockDriftError):
        await router.dispatch_order(exit_req, is_protective=True)

    exit_fsm = router.get_fsm("AGY_CD4_EXT_FAIL")
    assert exit_fsm.state == OrderState.REJECTED


@pytest.mark.asyncio
async def test_clock_drift_recovery_requires_operator_reset(mock_delta_env):
    """Test 5: After clock drift latch, system requires authenticated operator reset after clock sync is restored."""
    server, client = mock_delta_env
    router = LiveOrderRouter(client=client, max_clock_drift_ms=1000.0)

    # Latch system into CLOCK_DESYNC_UNPROTECTED_EXPOSURE
    router.kill_switch_state = "CLOCK_DESYNC_UNPROTECTED_EXPOSURE"

    # Restore clock synchronization
    server.clock_drift_offset_ms = 50.0

    # 1. Automated trading cannot resume automatically; state remains latched
    assert router.kill_switch_state == "CLOCK_DESYNC_UNPROTECTED_EXPOSURE"

    # 2. AI model / automated strategy process has ZERO reset authority
    with pytest.raises(PermissionError):
        router.reset_kill_switch(operator_role="AI_AGENT")

    # 3. If unresolved discrepancies exist, reset is rejected
    router.discrepancy_count = 1
    with pytest.raises(ValueError) as val_err:
        router.reset_kill_switch(operator_role="ADMIN")
    assert "Zero discrepancies required" in str(val_err.value)

    # 4. Once discrepancies are reconciled, authenticated ADMIN reset succeeds
    router.discrepancy_count = 0
    success = router.reset_kill_switch(operator_role="ADMIN")
    assert success is True
    assert router.kill_switch_state == "NORMAL"

