"""Unit tests for Delta REST API v2 Client and Mock Exchange Server."""

import asyncio
import pytest
from src.execution.delta.client import DeltaRestClient, TokenBucketRateLimiter
from src.execution.delta.mock_server import MockDeltaExchangeServer
from src.execution.delta.signer import DeltaRequestSigner
from src.execution.delta.types import (
    DeltaAuthenticationError,
    DeltaClockDriftError,
    DeltaConfig,
    DeltaEnvironment,
    DeltaLiveTradingDisabledError,
    DeltaNetworkError,
    DeltaOrderRejectedError,
    DeltaOrderRequest,
    DeltaOrderSide,
    DeltaOrderType,
    DeltaProductSpecificationError,
    DeltaRateLimitError,
    DeltaTimeInForce,
)


@pytest.mark.asyncio
async def test_token_bucket_rate_limiter():
    """Verify rate limiter token replenishment and consumption."""
    limiter = TokenBucketRateLimiter(rate_per_second=20.0, capacity=5.0)
    # Fast initial acquisition
    await limiter.acquire(1.0)
    await limiter.acquire(1.0)
    assert limiter.tokens <= 4.0

    # Deplete bucket
    await limiter.acquire(3.0)
    start = asyncio.get_event_loop().time()
    # Next acquisition forces wait
    await limiter.acquire(1.0)
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed >= 0.04  # ~0.05s required for 1 token at 20 tokens/sec


@pytest.mark.asyncio
async def test_rest_client_public_endpoints():
    """Verify querying public time and products endpoints via Mock Exchange."""
    mock_server = MockDeltaExchangeServer()
    transport = mock_server.create_mock_transport()
    config = DeltaConfig(environment=DeltaEnvironment.MOCK, base_url="https://mock.delta.exchange")

    async with DeltaRestClient(config=config, transport=transport) as client:
        server_time = await client.get_server_time()
        assert isinstance(server_time, int)
        assert server_time > 0

        products = await client.get_products()
        assert len(products) >= 2
        symbols = [p["symbol"] for p in products]
        assert "PAXGUSDT" in symbols
        assert "BTCUSDT" in symbols


@pytest.mark.asyncio
async def test_product_specification_discovery():
    """Verify parsing and validation of PAXGUSDT product specification."""
    mock_server = MockDeltaExchangeServer()
    transport = mock_server.create_mock_transport()
    config = DeltaConfig(environment=DeltaEnvironment.MOCK, base_url="https://mock.delta.exchange")

    async with DeltaRestClient(config=config, transport=transport) as client:
        spec = await client.get_product_by_symbol("PAXGUSDT")
        assert spec is not None
        assert spec.symbol == "PAXGUSDT"
        assert spec.contract_value == 0.001
        assert spec.tick_size == 0.01
        assert spec.quoting_asset == "USDT"
        assert spec.settling_asset == "USDT"
        assert spec.trading_status == "operational"

        unknown = await client.get_product_by_symbol("UNKNOWN_SYMBOL")
        assert unknown is None


@pytest.mark.asyncio
async def test_clock_drift_check_pass_and_breach():
    """Verify clock drift check passes under normal conditions and fails if drift > 1000ms."""
    mock_server = MockDeltaExchangeServer()
    transport = mock_server.create_mock_transport()
    config = DeltaConfig(
        environment=DeltaEnvironment.MOCK,
        base_url="https://mock.delta.exchange",
        clock_drift_max_ms=1000.0,
    )

    async with DeltaRestClient(config=config, transport=transport) as client:
        # Zero drift -> pass
        drift = await client.check_clock_drift()
        assert drift < 1000.0

        # Inject simulated 3000ms drift into mock server
        mock_server.clock_drift_offset_ms = 3500.0
        with pytest.raises(DeltaClockDriftError) as exc_info:
            await client.check_clock_drift()
        assert "exceeds safety SLA threshold" in str(exc_info.value)


@pytest.mark.asyncio
async def test_rate_limit_error_handling():
    """Verify HTTP 429 response is translated to DeltaRateLimitError."""
    mock_server = MockDeltaExchangeServer()
    mock_server.rate_limit_active = True
    mock_server.rate_limit_retry_after = 5.0
    transport = mock_server.create_mock_transport()
    config = DeltaConfig(environment=DeltaEnvironment.MOCK, base_url="https://mock.delta.exchange")

    async with DeltaRestClient(config=config, transport=transport) as client:
        with pytest.raises(DeltaRateLimitError) as exc_info:
            await client.get_products()
        assert "Rate limit exceeded" in str(exc_info.value)
        assert "5.0s" in str(exc_info.value)


@pytest.mark.asyncio
async def test_authentication_failure_handling():
    """Verify HTTP 401 response is translated to DeltaAuthenticationError."""
    mock_server = MockDeltaExchangeServer()
    mock_server.force_auth_failure = True
    transport = mock_server.create_mock_transport()
    config = DeltaConfig(
        environment=DeltaEnvironment.MOCK,
        base_url="https://mock.delta.exchange",
        api_key="wrong_key",
        api_secret="wrong_secret",
    )

    async with DeltaRestClient(config=config, transport=transport) as client:
        with pytest.raises(DeltaAuthenticationError) as exc_info:
            await client.get_balances()
        assert "Authentication rejected" in str(exc_info.value)


@pytest.mark.asyncio
async def test_network_timeout_handling():
    """Verify timeout is translated to DeltaNetworkError."""
    mock_server = MockDeltaExchangeServer()
    mock_server.force_network_timeout = True
    transport = mock_server.create_mock_transport()
    config = DeltaConfig(
        environment=DeltaEnvironment.MOCK,
        base_url="https://mock.delta.exchange",
        timeout_seconds=0.1,
    )

    async with DeltaRestClient(config=config, transport=transport) as client:
        with pytest.raises(DeltaNetworkError) as exc_info:
            await client.get_products()
        assert "Network transport error" in str(exc_info.value) or "timed out" in str(exc_info.value)


@pytest.mark.asyncio
async def test_authenticated_balances_and_orders_mock():
    """Verify order placement, balance retrieval, and cancellation on mock."""
    mock_server = MockDeltaExchangeServer(
        api_key="test_api_key",
        api_secret="test_api_secret_32_bytes_long_key",
    )
    transport = mock_server.create_mock_transport()
    config = DeltaConfig(
        environment=DeltaEnvironment.TESTNET,
        base_url="https://testnet-api.delta.exchange",
        api_key="test_api_key",
        api_secret="test_api_secret_32_bytes_long_key",
        live_trading_enabled=False,
    )

    async with DeltaRestClient(config=config, transport=transport) as client:
        balances = await client.get_balances()
        assert len(balances) >= 1
        assert balances[0]["asset_symbol"] == "USDT"

        # Place testnet order
        order_req = DeltaOrderRequest(
            product_id=1234,
            size=50,
            side=DeltaOrderSide.BUY,
            order_type=DeltaOrderType.LIMIT,
            limit_price=2650.0,
            time_in_force=DeltaTimeInForce.GTC,
            client_order_id="TEST_ORDER_001",
        )
        res = await client.place_order(order_req)
        assert res["id"] == 1001
        assert res["state"] == "open"
        assert res["client_order_id"] == "TEST_ORDER_001"

        # Query orders
        orders = await client.get_orders(product_id=1234)
        assert len(orders) == 1
        assert orders[0]["id"] == 1001

        # Cancel order
        cancel_res = await client.cancel_order(order_id=1001, product_id=1234)
        assert cancel_res["state"] == "cancelled"


@pytest.mark.asyncio
async def test_reduce_only_rejection_when_position_flat():
    """Verify that a reduce_only order rejected with ReduceOnlyExceedsPosition raises DeltaOrderRejectedError."""
    mock_server = MockDeltaExchangeServer(
        api_key="test_key",
        api_secret="test_secret_32_bytes_long_key",
    )
    # Position is currently 0
    transport = mock_server.create_mock_transport()
    config = DeltaConfig(
        environment=DeltaEnvironment.TESTNET,
        base_url="https://testnet-api.delta.exchange",
        api_key="test_key",
        api_secret="test_secret_32_bytes_long_key",
    )

    async with DeltaRestClient(config=config, transport=transport) as client:
        reduce_order = DeltaOrderRequest(
            product_id=1234,
            size=50,
            side=DeltaOrderSide.SELL,
            order_type=DeltaOrderType.MARKET,
            reduce_only=True,
            client_order_id="FLATTEN_ORDER_001",
        )
        with pytest.raises(DeltaOrderRejectedError) as exc_info:
            await client.place_order(reduce_order)
        assert exc_info.value.code == "ReduceOnlyExceedsPosition"


@pytest.mark.asyncio
async def test_fail_closed_production_live_trading_guard():
    """Verify that calling place_order in PRODUCTION environment when live_trading_enabled=False raises DeltaLiveTradingDisabledError."""
    config = DeltaConfig(
        environment=DeltaEnvironment.PRODUCTION,
        base_url="https://api.delta.exchange",
        api_key="prod_key",
        api_secret="prod_secret",
        live_trading_enabled=False,  # Hard-locked!
    )
    client = DeltaRestClient(config=config)
    order_req = DeltaOrderRequest(
        product_id=1234,
        size=10,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=2600.0,
    )

    with pytest.raises(DeltaLiveTradingDisabledError) as exc_info:
        await client.place_order(order_req)
    assert "LIVE_TRADING_ENABLED is False" in str(exc_info.value)


@pytest.mark.asyncio
async def test_secret_leakage_absence_in_errors_and_logs():
    """Verify that sensitive secrets are never leaked in error messages, exception strings, or configs."""
    super_secret = "my_ultra_confidential_secret_key_value"
    config = DeltaConfig(
        environment=DeltaEnvironment.TESTNET,
        api_key="my_secret_api_key_12345",
        api_secret=super_secret,
    )
    # Check config repr
    assert super_secret not in repr(config)
    assert "my_secret_api_key_12345" not in repr(config)

    # Check client and exceptions
    client = DeltaRestClient(config=config)
    assert super_secret not in repr(client.signer)

    # Force error and verify exception text
    mock_server = MockDeltaExchangeServer()
    mock_server.force_auth_failure = True
    client._transport = mock_server.create_mock_transport()

    with pytest.raises(DeltaAuthenticationError) as exc_info:
        await client.get_balances()
    assert super_secret not in str(exc_info.value)
    await client.close()


@pytest.mark.asyncio
async def test_fail_closed_production_cancel_methods_guard():
    """Verify P2-01: cancel_order and cancel_all_orders raise DeltaLiveTradingDisabledError in PRODUCTION when live_trading_enabled=False."""
    config = DeltaConfig(
        environment=DeltaEnvironment.PRODUCTION,
        base_url="https://api.delta.exchange",
        api_key="prod_key",
        api_secret="prod_secret",
        live_trading_enabled=False,  # Hard-locked!
    )
    client = DeltaRestClient(config=config)

    # 1. cancel_order
    with pytest.raises(DeltaLiveTradingDisabledError) as exc_info_1:
        await client.cancel_order(order_id=123, product_id=1234)
    assert "LIVE_TRADING_ENABLED is False" in str(exc_info_1.value)

    # 2. cancel_all_orders
    with pytest.raises(DeltaLiveTradingDisabledError) as exc_info_2:
        await client.cancel_all_orders(product_id=1234)
    assert "LIVE_TRADING_ENABLED is False" in str(exc_info_2.value)


@pytest.mark.asyncio
async def test_production_guard_prevents_http_dispatch():
    """Verify P2-01: Zero HTTP requests are dispatched to mock/server when production guard triggers."""
    mock_server = MockDeltaExchangeServer()
    transport = mock_server.create_mock_transport()
    config = DeltaConfig(
        environment=DeltaEnvironment.PRODUCTION,
        base_url="https://api.delta.exchange",
        api_key="prod_key",
        api_secret="prod_secret",
        live_trading_enabled=False,
    )
    client = DeltaRestClient(config=config, transport=transport)

    # Attempt place_order
    order_req = DeltaOrderRequest(
        product_id=1234,
        size=10,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.MARKET,
    )
    with pytest.raises(DeltaLiveTradingDisabledError):
        await client.place_order(order_req)

    # Attempt cancel_order
    with pytest.raises(DeltaLiveTradingDisabledError):
        await client.cancel_order(order_id=999, product_id=1234)

    # Attempt cancel_all_orders
    with pytest.raises(DeltaLiveTradingDisabledError):
        await client.cancel_all_orders()

    # Verify mock server recorded zero orders and zero cancellations
    assert len(mock_server.orders) == 0


@pytest.mark.asyncio
async def test_testnet_cancellation_available():
    """Verify P2-01: Cancellation remains functional in TESTNET environment."""
    mock_server = MockDeltaExchangeServer(
        api_key="test_key",
        api_secret="test_secret_32_bytes_long_key",
    )
    transport = mock_server.create_mock_transport()
    config = DeltaConfig(
        environment=DeltaEnvironment.TESTNET,
        base_url="https://testnet-api.delta.exchange",
        api_key="test_key",
        api_secret="test_secret_32_bytes_long_key",
        live_trading_enabled=False,
    )

    async with DeltaRestClient(config=config, transport=transport) as client:
        # Place testnet order
        order_req = DeltaOrderRequest(
            product_id=1234,
            size=20,
            side=DeltaOrderSide.BUY,
            order_type=DeltaOrderType.LIMIT,
            limit_price=2600.0,
        )
        res = await client.place_order(order_req)
        order_id = res["id"]

        # Cancel single order in TESTNET
        cancel_res = await client.cancel_order(order_id=order_id, product_id=1234)
        assert cancel_res["state"] == "cancelled"

        # Cancel all in TESTNET
        cancel_all_res = await client.cancel_all_orders(product_id=1234)
        assert cancel_all_res["success"] is True


@pytest.mark.asyncio
async def test_canonical_query_string_encoding_and_signing():
    """Verify P2-02: Query string canonicalization via urlencode with spaces, slashes, commas, and unicode."""
    mock_server = MockDeltaExchangeServer(
        api_key="query_key",
        api_secret="query_secret_32_bytes_long_key",
    )
    transport = mock_server.create_mock_transport()
    config = DeltaConfig(
        environment=DeltaEnvironment.TESTNET,
        base_url="https://testnet-api.delta.exchange",
        api_key="query_key",
        api_secret="query_secret_32_bytes_long_key",
    )

    async with DeltaRestClient(config=config, transport=transport) as client:
        # Complex parameters testing URL encoding
        params = {
            "symbol": "PAXG/USDT",
            "search": "Gold Bullion 999",
            "tags": "spot,safe-haven",
            "note": "über-tier",
            "empty": None,  # Filtered out
        }
        # Execute request through client._request
        data = await client._request("GET", "/v2/products", params=params, authenticated=True)
        assert isinstance(data, dict)
        assert "result" in data


@pytest.mark.asyncio
async def test_product_specification_validation_strictness():
    """Verify P3-01: get_product_by_symbol strictly validates metadata and raises DeltaProductSpecificationError on invalid fields."""
    mock_server = MockDeltaExchangeServer()
    transport = mock_server.create_mock_transport()
    config = DeltaConfig(environment=DeltaEnvironment.MOCK, base_url="https://mock.delta.exchange")

    async with DeltaRestClient(config=config, transport=transport) as client:
        # 1. Missing contract_value
        bad_prod_1 = {"id": 101, "symbol": "BAD1", "tick_size": "0.01", "min_order_size": 1}
        with pytest.raises(DeltaProductSpecificationError) as exc:
            client._validate_product_specification(bad_prod_1)
        assert "missing required 'contract_value'" in str(exc.value)

        # 2. Non-positive contract_value
        bad_prod_2 = {
            "id": 102,
            "symbol": "BAD2",
            "contract_value": "0.0",
            "tick_size": "0.01",
            "min_order_size": 1,
        }
        with pytest.raises(DeltaProductSpecificationError) as exc:
            client._validate_product_specification(bad_prod_2)
        assert "non-positive/non-finite 'contract_value'" in str(exc.value)

        # 3. Missing tick_size
        bad_prod_3 = {
            "id": 103,
            "symbol": "BAD3",
            "contract_value": "0.001",
            "min_order_size": 1,
        }
        with pytest.raises(DeltaProductSpecificationError) as exc:
            client._validate_product_specification(bad_prod_3)
        assert "missing required 'tick_size'" in str(exc.value)

        # 4. min_order_size < 1
        bad_prod_4 = {
            "id": 104,
            "symbol": "BAD4",
            "contract_value": "0.001",
            "tick_size": "0.01",
            "min_order_size": 0,
        }
        with pytest.raises(DeltaProductSpecificationError) as exc:
            client._validate_product_specification(bad_prod_4)
        assert "invalid 'min_order_size'" in str(exc.value)

        # 5. max_order_size < min_order_size
        bad_prod_5 = {
            "id": 105,
            "symbol": "BAD5",
            "contract_value": "0.001",
            "tick_size": "0.01",
            "min_order_size": 10,
            "max_order_size": 5,
        }
        with pytest.raises(DeltaProductSpecificationError) as exc:
            client._validate_product_specification(bad_prod_5)
        assert "invalid 'max_order_size'" in str(exc.value)

        # 6. Missing symbol or id
        with pytest.raises(DeltaProductSpecificationError) as exc:
            client._validate_product_specification({})
        assert "missing valid 'symbol'" in str(exc.value)


