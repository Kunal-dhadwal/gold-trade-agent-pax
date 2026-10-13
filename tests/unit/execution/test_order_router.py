"""Unit tests for LiveOrderRouter, ClientOrderIdGenerator, and IdempotencyManager (Sub-Milestone 4.2.2)."""

import asyncio
import re
import pytest
import httpx

from src.execution.delta.client import DeltaRestClient
from src.execution.delta.fsm import OrderState
from src.execution.delta.idempotency import ClientOrderIdGenerator, IdempotencyManager
from src.execution.delta.mock_server import MockDeltaExchangeServer
from src.execution.delta.order_router import LiveOrderRouter
from src.execution.delta.types import (
    DeltaAuthenticationError,
    DeltaClockDriftError,
    DeltaConfig,
    DeltaConnectorError,
    DeltaEnvironment,
    DeltaNetworkError,
    DeltaOrderRejectedError,
    DeltaOrderRequest,
    DeltaOrderSide,
    DeltaOrderType,
    DeltaRateLimitError,
    DeltaTimeInForce,
    DeltaTransportOrTimeSyncUnavailableError,
    DiscrepancyBlockedError,
    IdempotencyConflictError,
    InterPositionEntryBlockedError,
    LeadershipLostError,
    PositionBoundsExceededError,
)


# --- ClientOrderIdGenerator Tests ---

def test_client_order_id_generation_and_validation():
    """Verify ClientOrderIdGenerator format, length <= 32, and regex validation."""
    cid = ClientOrderIdGenerator.generate(strategy_id="TF_01", bar_timestamp=1728640000)
    assert len(cid) <= 32
    assert ClientOrderIdGenerator.validate(cid)
    assert cid.startswith("AGY_TF01_1728640000_")

    # Custom nonce
    cid_custom = ClientOrderIdGenerator.generate(strategy_id="MEANREV", bar_timestamp=1728640000, nonce="ABC123")
    assert len(cid_custom) <= 32
    assert cid_custom.endswith("_ABC123")
    assert ClientOrderIdGenerator.validate(cid_custom)

    # Strategy ID longer than 6 characters must be truncated to 6
    cid_long = ClientOrderIdGenerator.generate(strategy_id="VERYLONGSTRATEGYNAME", bar_timestamp=1728640000)
    assert len(cid_long) <= 32
    assert cid_long.startswith("AGY_VERYLO_")
    assert ClientOrderIdGenerator.validate(cid_long)

    # Timestamp with 13 digits (ms)
    cid_ms = ClientOrderIdGenerator.generate(strategy_id="STRAT", bar_timestamp=1728640000123)
    assert len(cid_ms) <= 32
    assert ClientOrderIdGenerator.validate(cid_ms)

    # Invalid client order IDs
    assert not ClientOrderIdGenerator.validate("")
    assert not ClientOrderIdGenerator.validate("AGY_TOO_LONG_STRING_THAT_EXCEEDS_32_CHARACTERS_HERE")
    assert not ClientOrderIdGenerator.validate("INVALID_FORMAT")
    assert not ClientOrderIdGenerator.validate("agy_lowercase_123_abc")


# --- IdempotencyManager Tests ---

@pytest.mark.asyncio
async def test_idempotency_manager_duplicate_and_conflict():
    """Verify IdempotencyManager allows duplicate identical submissions but raises on conflicts."""
    mgr = IdempotencyManager()
    payload = {
        "symbol": "PAXGUSDT",
        "size": 5,
        "side": "BUY",
        "order_type": "limit_order",
        "reduce_only": False,
    }

    record, is_dup = await mgr.register_order("AGY_T1_100_A1", payload)
    assert not is_dup
    assert record["client_order_id"] == "AGY_T1_100_A1"

    # Exact duplicate registration returns is_dup=True
    dup_record, is_dup_2 = await mgr.register_order("AGY_T1_100_A1", payload)
    assert is_dup_2
    assert dup_record["client_order_id"] == "AGY_T1_100_A1"

    # Conflicting payload (different size) under same client_order_id raises IdempotencyConflictError
    conflicting_payload = dict(payload, size=10)
    with pytest.raises(IdempotencyConflictError):
        await mgr.register_order("AGY_T1_100_A1", conflicting_payload)


# --- LiveOrderRouter Tests ---

@pytest.fixture
def mock_delta():
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
async def test_router_idempotent_order_dispatch(mock_delta):
    """Verify router dispatches only once to exchange matching engine on duplicate client order IDs."""
    server, client = mock_delta
    router = LiveOrderRouter(client=client)

    request = DeltaOrderRequest(
        product_id=1234,
        size=2,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2650.0,
        client_order_id="AGY_IDEM_1000_1",
    )

    # First dispatch submits to mock server
    res1 = await router.dispatch_order(request)
    assert res1.get("id") is not None
    assert len(server.orders) == 1

    # Second dispatch with identical client_order_id returns cached record without new exchange order
    res2 = await router.dispatch_order(request)
    assert res2.get("id") == res1.get("id")
    assert len(server.orders) == 1  # No duplicate order created


@pytest.mark.asyncio
async def test_router_leadership_fencing(mock_delta):
    """Verify router strictly halts all order dispatch when node lacks valid leadership lease."""
    _, client = mock_delta
    # Leadership function returns False
    router = LiveOrderRouter(client=client, is_leader_fn=lambda: False)

    request = DeltaOrderRequest(
        product_id=1234,
        size=2,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.MARKET,
        client_order_id="AGY_LEAD_1000_1",
    )

    with pytest.raises(LeadershipLostError):
        await router.dispatch_order(request)

    fsm = router.get_fsm("AGY_LEAD_1000_1")
    assert fsm is not None
    assert fsm.state == OrderState.REJECTED


@pytest.mark.asyncio
async def test_inter_position_entry_blocking(mock_delta):
    """Verify router blocks new entries if companion exit from prior trade is active/unconfirmed."""
    server, client = mock_delta
    server.positions[1234] = {"size": 2, "entry_price": 2600.0}
    router = LiveOrderRouter(client=client)

    # Place an exit order (e.g. resting stop loss)
    exit_request = DeltaOrderRequest(
        product_id=1234,
        size=2,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2700.0,
        reduce_only=True,
        client_order_id="AGY_EXIT_1000_1",
    )
    await router.dispatch_order(exit_request, is_protective=True)
    exit_fsm = router.get_fsm("AGY_EXIT_1000_1")
    assert exit_fsm.is_active

    # Attempt to place a new entry order while exit order is active
    entry_request = DeltaOrderRequest(
        product_id=1234,
        size=5,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2640.0,
        reduce_only=False,
        client_order_id="AGY_ENTR_1000_1",
    )

    with pytest.raises(InterPositionEntryBlockedError):
        await router.dispatch_order(entry_request, is_protective=False)

    entry_fsm = router.get_fsm("AGY_ENTR_1000_1")
    assert entry_fsm.state == OrderState.REJECTED


@pytest.mark.asyncio
async def test_protective_exit_bypasses_entry_gates(mock_delta):
    """Verify protective exits bypass entry gates (Gates 2, 5, 6, 7, 11) while entries are rejected."""
    server, client = mock_delta

    # Mock gate evaluator that rejects entry orders on Gate 2, 5, 6, 7, or 11
    async def mock_gate_eval(ctx):
        if not ctx.get("is_protective"):
            return {"approved": False, "failed_gate": 11, "message": "Fee cache stale (Gate 11)"}
        return {"approved": True}

    router = LiveOrderRouter(client=client, risk_gate_evaluator=mock_gate_eval)

    # 1. Entry order is rejected by Gate 11
    entry_request = DeltaOrderRequest(
        product_id=1234,
        size=1,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2600.0,
        reduce_only=False,
        client_order_id="AGY_GATE_ENT_1",
    )
    with pytest.raises(DeltaOrderRejectedError):
        await router.dispatch_order(entry_request, is_protective=False)

    # 2. Protective exit order bypasses Gate 11 and dispatches successfully
    server.positions[1234] = {"size": 10}
    exit_request = DeltaOrderRequest(
        product_id=1234,
        size=1,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2650.0,
        reduce_only=True,
        client_order_id="AGY_GATE_EXT_1",
    )
    res = await router.dispatch_order(exit_request, is_protective=True)
    assert res.get("id") is not None
    exit_fsm = router.get_fsm("AGY_GATE_EXT_1")
    assert exit_fsm.state == OrderState.ACCEPTED


@pytest.mark.asyncio
async def test_protective_exit_enforces_gate_1_drawdown(mock_delta):
    """Verify protective exit strictly enforces Gate 1 portfolio drawdown cap >= 6.0% and latches FLATTENING."""
    server, client = mock_delta
    server.positions[1234] = {"size": 10}

    async def mock_gate_eval(ctx):
        if ctx.get("is_protective"):
            return {"approved": True, "gate_1_drawdown_breach": True}
        return {"approved": True}

    router = LiveOrderRouter(client=client, risk_gate_evaluator=mock_gate_eval)

    exit_request = DeltaOrderRequest(
        product_id=1234,
        size=1,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.MARKET,
        reduce_only=True,
        client_order_id="AGY_DRAW_EXT_1",
    )
    await router.dispatch_order(exit_request, is_protective=True)
    assert router.kill_switch_state == "FLATTENING"


@pytest.mark.asyncio
async def test_dynamic_bracket_scaling(mock_delta):
    """Verify dynamic bracket scaling: Stop Loss quantity Q_SL = Q_filled and Q_SL <= Q_filled."""
    _, client = mock_delta
    router = LiveOrderRouter(client=client)

    # Simulate entry order for 10 lots
    entry_cid = "AGY_BRKT_ENT_1"
    sl_cid = "AGY_BRKT_SL_1"
    tp_cid = "AGY_BRKT_TP_1"

    router._order_records[sl_cid] = {"size": 10.0}
    router._order_records[tp_cid] = {"size": 10.0}

    # Partial fill of 4 lots arrives
    res = await router.handle_partial_fill(
        entry_client_order_id=entry_cid,
        filled_quantity=4.0,
        sl_client_order_id=sl_cid,
        tp_client_order_id=tp_cid,
    )

    assert res["q_filled"] == 4.0
    assert res["q_sl"] == 4.0
    assert res["q_tp"] == 4.0
    assert router._order_records[sl_cid]["size"] == 4.0
    assert router._order_records[tp_cid]["size"] == 4.0
    # Stop-loss size must never exceed filled quantity
    assert router._order_records[sl_cid]["size"] <= res["q_filled"]


@pytest.mark.asyncio
async def test_synthetic_oco_companion_cancellation(mock_delta):
    """Verify synthetic OCO: when Stop Loss fills, companion Take Profit order is cancelled."""
    server, client = mock_delta
    router = LiveOrderRouter(client=client)

    sl_cid = "AGY_OCO_SL_1"
    tp_cid = "AGY_OCO_TP_1"

    # Set existing position for reduce_only exit order
    server.positions[1234] = {"size": 10}

    # Place TP order on exchange
    tp_req = DeltaOrderRequest(
        product_id=1234,
        size=2,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2750.0,
        reduce_only=True,
        client_order_id=tp_cid,
    )
    tp_res = await router.dispatch_order(tp_req, is_protective=True)
    tp_exchange_id = tp_res["id"]
    assert server.orders[tp_exchange_id]["state"] == "open"

    # Pair SL and TP in router
    await router.handle_partial_fill("AGY_OCO_ENT_1", 2.0, sl_cid, tp_cid)

    # Simulate SL execution
    cancelled_id = await router.handle_protective_fill(sl_cid, product_id=1234)
    assert cancelled_id == tp_cid
    assert server.orders[tp_exchange_id]["state"] == "cancelled"

    tp_fsm = router.get_fsm(tp_cid)
    assert tp_fsm.state == OrderState.CANCELLED


@pytest.mark.asyncio
async def test_network_timeout_handling_zero_retry(mock_delta):
    """Verify network timeout transitions to FAILED -> RECONCILING with ZERO automatic retry."""
    server, client = mock_delta
    server.force_network_timeout = True
    router = LiveOrderRouter(client=client, clock_drift_evaluator=lambda: 50.0)

    request = DeltaOrderRequest(
        product_id=1234,
        size=1,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2600.0,
        client_order_id="AGY_TO_1000_1",
    )

    with pytest.raises(DeltaNetworkError):
        await router.dispatch_order(request)

    fsm = router.get_fsm("AGY_TO_1000_1")
    assert fsm is not None
    assert fsm.state == OrderState.RECONCILING
    # Transitions should be CREATED -> SIGNED -> SUBMITTED -> FAILED -> RECONCILING
    states = [h["to_state"] for h in fsm.history]
    assert states == ["SIGNED", "SUBMITTED", "FAILED", "RECONCILING"]


@pytest.mark.asyncio
async def test_reduce_only_exceeds_position_handling(mock_delta):
    """Verify ReduceOnlyExceedsPosition transitions order to CANCELLED and schedules reconciliation."""
    server, client = mock_delta
    # Ensure mock position is 0
    server.positions[1234] = {"size": 0}
    router = LiveOrderRouter(client=client)

    request = DeltaOrderRequest(
        product_id=1234,
        size=5,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2700.0,
        reduce_only=True,
        client_order_id="AGY_RED_EXC_1",
    )

    res = await router.dispatch_order(request, is_protective=True)
    assert res["state"] == OrderState.CANCELLED.value
    assert res["error"] == "ReduceOnlyExceedsPosition"

    fsm = router.get_fsm("AGY_RED_EXC_1")
    assert fsm.state == OrderState.CANCELLED
    assert router.discrepancy_count == 1


@pytest.mark.asyncio
async def test_operator_kill_switch_reset_preconditions(mock_delta):
    """Verify operator Kill Switch reset enforces ADMIN role and 0 discrepancies."""
    _, client = mock_delta
    router = LiveOrderRouter(client=client)
    router.kill_switch_state = "FLATTENING"
    router.discrepancy_count = 2

    # Non-admin rejected
    with pytest.raises(PermissionError):
        router.reset_kill_switch(operator_role="AI_AGENT")

    # Non-zero discrepancies rejected
    with pytest.raises(ValueError):
        router.reset_kill_switch(operator_role="ADMIN")

    # Discrepancies cleared -> reset succeeds
    router.discrepancy_count = 0
    assert router.reset_kill_switch(operator_role="ADMIN")
    assert router.kill_switch_state == "NORMAL"


@pytest.mark.asyncio
async def test_operator_flatten_success_flow(mock_delta):
    """Verify in-band operator flatten endpoint executes successfully when prerequisites are satisfied."""
    server, client = mock_delta
    server.positions[1234] = {"size": 4, "entry_price": 2650.0}
    router = LiveOrderRouter(client=client, clock_drift_evaluator=lambda: 50.0)

    res = await router.operator_flatten(product_id=1234, size=4, side=DeltaOrderSide.SELL)
    assert res.get("id") is not None
    assert res.get("reduce_only") is True
    assert res.get("side") == "sell"


@pytest.mark.asyncio
async def test_operator_flatten_leadership_failure(mock_delta):
    """Verify operator flatten fails fast with 503 error when leadership is not held."""
    _, client = mock_delta
    router = LiveOrderRouter(client=client, is_leader_fn=lambda: False)

    with pytest.raises(DeltaTransportOrTimeSyncUnavailableError) as exc_info:
        await router.operator_flatten(product_id=1234, size=4, side=DeltaOrderSide.SELL)

    assert "Delta Web Console out-of-band" in str(exc_info.value)


@pytest.mark.asyncio
async def test_idempotency_manager_helpers():
    """Verify IdempotencyManager get_order and update_state helper methods."""
    mgr = IdempotencyManager()
    payload = {"symbol": "PAXGUSDT", "size": 1, "side": "BUY", "state": "CREATED"}
    await mgr.register_order("AGY_TEST_HLP_1", payload)

    retrieved = await mgr.get_order("AGY_TEST_HLP_1")
    assert retrieved is not None
    assert retrieved["symbol"] == "PAXGUSDT"

    await mgr.update_state("AGY_TEST_HLP_1", "ACCEPTED")
    updated = await mgr.get_order("AGY_TEST_HLP_1")
    assert updated["state"] == "ACCEPTED"

    # Non-existent ID returns None
    assert await mgr.get_order("AGY_NON_EXISTENT") is None


def test_client_order_id_edge_cases():
    """Verify edge cases for ClientOrderIdGenerator: empty strategy, non-int timestamp, long budget."""
    # Empty strategy falls back to DEF
    cid_empty = ClientOrderIdGenerator.generate(strategy_id="", bar_timestamp=1728640000)
    assert "AGY_DEF_" in cid_empty

    # Non-int timestamp string handled gracefully
    cid_non_int = ClientOrderIdGenerator.generate(strategy_id="TF", bar_timestamp="invalid_ts")
    assert cid_non_int.startswith("AGY_TF_")
    assert ClientOrderIdGenerator.validate(cid_non_int)

    # Long timestamp budget clamping
    cid_huge = ClientOrderIdGenerator.generate(
        strategy_id="STRAT6",
        bar_timestamp="1234567890123456789012345",
        nonce="NONCE123",
    )
    assert len(cid_huge) <= 32
    assert ClientOrderIdGenerator.validate(cid_huge)


@pytest.mark.asyncio
async def test_in_flight_concurrent_duplicate_submission_awaits_primary(mock_delta):
    """Verify concurrent duplicate request during network submission awaits primary resolution."""
    server, client = mock_delta
    router = LiveOrderRouter(client=client)

    request = DeltaOrderRequest(
        product_id=1234,
        size=2,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2600.0,
        client_order_id="AGY_CONC_DUP_1",
    )

    # Launch two concurrent dispatches with identical client_order_id
    res1, res2 = await asyncio.gather(
        router.dispatch_order(request),
        router.dispatch_order(request),
    )

    # Both must resolve to the identical confirmed exchange order
    assert res1["client_order_id"] == "AGY_CONC_DUP_1"
    assert res2["client_order_id"] == "AGY_CONC_DUP_1"
    assert res1["id"] == res2["id"]
    assert res1["state"] == OrderState.ACCEPTED.value
    assert res2["state"] == OrderState.ACCEPTED.value

    # Exactly 1 order was placed on the exchange matching engine
    matching_orders = [o for o in server.orders.values() if o.get("client_order_id") == "AGY_CONC_DUP_1"]
    assert len(matching_orders) == 1


@pytest.mark.asyncio
async def test_exchange_side_bracket_scaling_cancel_replace(mock_delta):
    """Verify dynamic bracket scaling executes exchange-side cancel/replace so Q_SL <= Q_filled on matching engine."""
    server, client = mock_delta
    router = LiveOrderRouter(client=client)

    server.positions[1234] = {"size": 10}

    # 1. Place initial Stop Loss on exchange for 10 lots
    sl_cid = "AGY_EX_SL_1"
    sl_req = DeltaOrderRequest(
        product_id=1234,
        size=10,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.STOP_MARKET,
        stop_price=2500.0,
        reduce_only=True,
        client_order_id=sl_cid,
    )
    sl_res = await router.dispatch_order(sl_req, is_protective=True)
    old_sl_id = sl_res["id"]
    assert server.orders[old_sl_id]["state"] == "open"
    assert server.orders[old_sl_id]["size"] == 10

    # 2. Partial fill of 4 lots arrives on entry
    entry_cid = "AGY_EX_ENT_1"
    res = await router.handle_partial_fill(
        entry_client_order_id=entry_cid,
        filled_quantity=4.0,
        sl_client_order_id=sl_cid,
    )

    # 3. Old stop order on exchange must be cancelled
    assert server.orders[old_sl_id]["state"] == "cancelled"

    # 4. A replacement stop order must be placed on exchange matching engine for exactly 4 lots
    active_sl_cid = router._bracket_pairs[entry_cid]["sl_id"]
    active_sl_rec = router._order_records[active_sl_cid]
    new_sl_id = active_sl_rec["id"]

    assert server.orders[new_sl_id]["state"] == "open"
    assert server.orders[new_sl_id]["size"] == 4
    assert server.orders[new_sl_id]["reduce_only"] is True

    # Invariant verified on exchange matching engine: Q_SL <= Q_filled
    assert server.orders[new_sl_id]["size"] <= res["q_filled"]


@pytest.mark.asyncio
async def test_oco_companion_cancellation_handles_dual_fill(mock_delta):
    """Verify synthetic OCO handles fast-market dual fill by recording FILLED and incrementing discrepancy count."""
    server, client = mock_delta
    router = LiveOrderRouter(client=client)

    server.positions[1234] = {"size": 5}
    sl_cid = "AGY_DUAL_SL_1"
    tp_cid = "AGY_DUAL_TP_1"

    # Dispatch TP order
    tp_req = DeltaOrderRequest(
        product_id=1234,
        size=5,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2750.0,
        reduce_only=True,
        client_order_id=tp_cid,
    )
    tp_res = await router.dispatch_order(tp_req, is_protective=True)
    tp_order_id = tp_res["id"]

    # Dispatch SL order
    sl_req = DeltaOrderRequest(
        product_id=1234,
        size=5,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.STOP_MARKET,
        stop_price=2600.0,
        reduce_only=True,
        client_order_id=sl_cid,
    )
    await router.dispatch_order(sl_req, is_protective=True)

    # Pair them
    router._companion_map[sl_cid] = tp_cid
    router._companion_map[tp_cid] = sl_cid

    # Simulate fast market where TP ALREADY FILLED on exchange just before SL triggered
    server.orders[tp_order_id]["state"] = "filled"

    # SL fills
    initial_discrepancies = router.discrepancy_count
    cancelled_id = await router.handle_protective_fill(sl_cid, product_id=1234)

    assert cancelled_id == tp_cid
    # TP must NOT be falsely marked as CANCELLED; it must reflect FILLED
    tp_fsm = router.get_fsm(tp_cid)
    assert tp_fsm.state == OrderState.FILLED

    # Unresolved discrepancy must be recorded
    assert router.discrepancy_count == initial_discrepancies + 1


@pytest.mark.asyncio
async def test_discrepancy_count_blocks_new_entry(mock_delta):
    """Verify unresolved reconciliation discrepancies strictly block new entry orders."""
    _, client = mock_delta
    router = LiveOrderRouter(client=client)

    # Simulate unresolved discrepancy
    router.discrepancy_count = 1

    entry_req = DeltaOrderRequest(
        product_id=1234,
        size=1,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2600.0,
        client_order_id="AGY_DISC_ENT_1",
    )

    with pytest.raises(DiscrepancyBlockedError) as exc_info:
        await router.dispatch_order(entry_req, is_protective=False)

    assert "unresolved reconciliation discrepancies" in str(exc_info.value)
    fsm = router.get_fsm("AGY_DISC_ENT_1")
    assert fsm.state == OrderState.REJECTED


@pytest.mark.asyncio
async def test_position_bounds_check_blocks_excess_protective_exit(mock_delta):
    """Verify protective exit order exceeding known position inventory is blocked locally."""
    server, client = mock_delta
    # Known position is 2 contracts
    server.positions[1234] = {"size": 2}

    router = LiveOrderRouter(client=client)

    # Protective exit requests 5 contracts (> 2)
    exit_req = DeltaOrderRequest(
        product_id=1234,
        size=5,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.MARKET,
        reduce_only=True,
        client_order_id="AGY_BND_EXT_1",
    )

    with pytest.raises(PositionBoundsExceededError) as exc_info:
        await router.dispatch_order(exit_req, is_protective=True)

    assert "exceeds known position" in str(exc_info.value)
    fsm = router.get_fsm("AGY_BND_EXT_1")
    assert fsm.state == OrderState.REJECTED
    assert len(server.orders) == 0


@pytest.mark.asyncio
async def test_rate_limit_and_auth_failures_transition_to_rejected(mock_delta):
    """Verify rate limit (429) and auth (401) failures transition to REJECTED."""
    server, client = mock_delta
    router = LiveOrderRouter(client=client, clock_drift_evaluator=lambda: 50.0)

    # 1. Rate Limit (HTTP 429)
    server.rate_limit_active = True
    req_429 = DeltaOrderRequest(
        product_id=1234,
        size=1,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2600.0,
        client_order_id="AGY_RL_1",
    )

    with pytest.raises(DeltaRateLimitError):
        await router.dispatch_order(req_429)

    fsm_429 = router.get_fsm("AGY_RL_1")
    assert fsm_429.state == OrderState.REJECTED

    # 2. Auth Failure (HTTP 401)
    server.rate_limit_active = False
    server.force_auth_failure = True

    req_401 = DeltaOrderRequest(
        product_id=1234,
        size=1,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2600.0,
        client_order_id="AGY_AUTH_1",
    )

    with pytest.raises(DeltaAuthenticationError):
        await router.dispatch_order(req_401)

    fsm_401 = router.get_fsm("AGY_AUTH_1")
    assert fsm_401.state == OrderState.REJECTED


