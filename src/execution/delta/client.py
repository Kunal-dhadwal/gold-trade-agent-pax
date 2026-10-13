"""Authenticated asynchronous REST API v2 client for Delta Exchange."""

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
import httpx
from src.core.logger import logger
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
    DeltaProductSpecification,
    DeltaProductSpecificationError,
    DeltaRateLimitError,
)


class TokenBucketRateLimiter:
    """Token-bucket rate limiter for client-side API throttling."""

    def __init__(self, rate_per_second: float = 10.0, capacity: float = 20.0):
        self.rate = rate_per_second
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0) -> None:
        """Acquire tokens, asynchronously sleeping if bucket is depleted."""
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_update
                self.last_update = now
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

                if self.tokens >= cost:
                    self.tokens -= cost
                    return
                # Calculate sleep duration needed to replenish required tokens
                wait_time = (cost - self.tokens) / self.rate

            await asyncio.sleep(wait_time)


class DeltaRestClient:
    """Asynchronous Delta Exchange REST v2 Client.
    
    Guarantees:
    - Environment separation (Testnet default vs Production).
    - Hard-coded live trading guard preventing production orders when disabled.
    - Clock drift tracking and fail-closed SLA enforcement.
    - Client-side token-bucket rate limiting.
    - Zero credential/secret leakage in logs and exceptions.
    """

    def __init__(
        self,
        config: Optional[DeltaConfig] = None,
        signer: Optional[DeltaRequestSigner] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.config = config or DeltaConfig()
        if signer is not None:
            self.signer = signer
        elif self.config.api_key and self.config.api_secret:
            self.signer = DeltaRequestSigner(self.config.api_key, self.config.api_secret)
        else:
            self.signer = None

        self.rate_limiter = TokenBucketRateLimiter(
            rate_per_second=self.config.rate_limit_per_second,
            capacity=self.config.rate_limit_per_second * 2,
        )
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._last_server_time_drift_ms: Optional[float] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or initialize asynchronous HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url.rstrip("/"),
                timeout=self.config.timeout_seconds,
                transport=self._transport,
                headers={"User-Agent": "AGentTrXPAUS-Quant/1.0"},
            )
        return self._client

    async def close(self) -> None:
        """Close underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "DeltaRestClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    def _redact_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        """Redact sensitive auth tokens from header dictionary."""
        redacted = dict(headers)
        if "api-key" in redacted:
            key = redacted["api-key"]
            redacted["api-key"] = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "***"
        if "signature" in redacted:
            redacted["signature"] = "[REDACTED]"
        return redacted

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        authenticated: bool = False,
    ) -> Dict[str, Any]:
        """Execute rate-limited, authenticated HTTP request with error translation."""
        await self.rate_limiter.acquire(1.0)
        client = await self._get_client()

        # Deterministic standards-compliant query string canonicalization (sorted keys)
        query_string = ""
        if params:
            sorted_params = [(k, str(v)) for k, v in sorted(params.items()) if v is not None]
            if sorted_params:
                query_string = "?" + urlencode(sorted_params)

        body_string = ""
        if json_data:
            body_string = json.dumps(json_data, separators=(",", ":"))

        query_or_body = query_string if query_string else body_string

        headers: Dict[str, str] = {}
        if authenticated:
            if self.signer is None:
                raise DeltaAuthenticationError("Authenticated request requested but no API credentials configured")
            ts = self.signer.get_current_timestamp_ms()
            auth_headers = self.signer.build_rest_headers(
                method=method,
                path=path,
                query_or_body=query_or_body,
                timestamp_ms=ts,
            )
            headers.update(auth_headers)

        if json_data is not None:
            headers["Content-Type"] = "application/json"

        # Construct URL with canonical query string to guarantee byte-for-byte signing consistency
        url = f"{path}{query_string}" if query_string else path
        try:
            response = await client.request(
                method=method,
                url=url,
                content=body_string.encode("utf-8") if json_data is not None else None,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise DeltaNetworkError(f"HTTP request timed out connecting to {self.config.base_url}{path}") from exc
        except httpx.RequestError as exc:
            raise DeltaNetworkError(f"Network transport error: {type(exc).__name__}") from exc

        # Track server clock drift via Date header if present
        if "date" in response.headers:
            try:
                server_date = parsedate_to_datetime(response.headers["date"])
                server_ms = server_date.timestamp() * 1000
                client_ms = time.time() * 1000
                self._last_server_time_drift_ms = abs(client_ms - server_ms)
            except Exception:
                pass

        # Error response handling
        if response.status_code == 401 or response.status_code == 403:
            raise DeltaAuthenticationError(f"Authentication rejected by Delta Exchange (HTTP {response.status_code})")
        elif response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "1.0")
            raise DeltaRateLimitError(f"Rate limit exceeded (HTTP 429). Retry after {retry_after}s")
        elif response.status_code >= 400:
            try:
                error_body = response.json()
            except Exception:
                error_body = {"raw": response.text}
            
            error_msg = error_body.get("error", {}).get("message") or error_body.get("message") or str(error_body)
            error_code = error_body.get("error", {}).get("code") or error_body.get("code")
            raise DeltaOrderRejectedError(
                message=f"Delta Exchange API error (HTTP {response.status_code}): {error_msg}",
                code=error_code,
                details=error_body,
            )

        try:
            return response.json()
        except Exception as exc:
            raise DeltaConnectorError(f"Failed to parse JSON response from {path}") from exc

    # Public API Methods
    async def get_server_time(self) -> int:
        """Fetch server time from exchange or server headers (in ms)."""
        data = await self._request("GET", "/v2/time", authenticated=False)
        result = data.get("result", {})
        if isinstance(result, dict) and "time" in result:
            return int(result["time"])
        elif isinstance(data.get("result"), (int, float)):
            return int(data["result"])
        raise DeltaConnectorError("Invalid time payload received from /v2/time")

    async def check_clock_drift(self) -> float:
        """Verify clock drift between local system and Delta Exchange.
        
        Returns clock drift in milliseconds.
        Raises DeltaClockDriftError if drift exceeds clock_drift_max_ms.
        """
        start = time.time() * 1000
        server_ms = await self.get_server_time()
        end = time.time() * 1000
        mid = (start + end) / 2.0

        drift_ms = abs(mid - server_ms)
        self._last_server_time_drift_ms = drift_ms

        if drift_ms > self.config.clock_drift_max_ms:
            raise DeltaClockDriftError(
                f"Clock drift ({drift_ms:.1f}ms) exceeds safety SLA threshold ({self.config.clock_drift_max_ms:.1f}ms)"
            )
        return drift_ms

    async def get_products(self) -> List[Dict[str, Any]]:
        """Query list of all tradeable products from /v2/products."""
        data = await self._request("GET", "/v2/products", authenticated=False)
        return data.get("result", [])

    def _validate_product_specification(self, p: Dict[str, Any]) -> DeltaProductSpecification:
        """Strictly validate exchange product metadata, raising DeltaProductSpecificationError on missing or invalid fields."""
        symbol = p.get("symbol")
        if not symbol or not isinstance(symbol, str):
            raise DeltaProductSpecificationError("Product payload missing valid 'symbol' field")

        # Product ID
        prod_id = p.get("id")
        if prod_id is None:
            raise DeltaProductSpecificationError(f"Product {symbol} missing required 'id' field")
        try:
            prod_id_int = int(prod_id)
            if prod_id_int <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            raise DeltaProductSpecificationError(f"Product {symbol} has invalid non-positive 'id': {prod_id}")

        # Contract Multiplier / Value (must be > 0, finite float)
        if "contract_value" not in p or p["contract_value"] is None:
            raise DeltaProductSpecificationError(f"Product {symbol} missing required 'contract_value'")
        try:
            contract_value = float(p["contract_value"])
            if not math.isfinite(contract_value) or contract_value <= 0.0:
                raise ValueError()
        except (ValueError, TypeError):
            raise DeltaProductSpecificationError(
                f"Product {symbol} has invalid non-positive/non-finite 'contract_value': {p.get('contract_value')}"
            )

        # Tick Size (must be > 0, finite float)
        if "tick_size" not in p or p["tick_size"] is None:
            raise DeltaProductSpecificationError(f"Product {symbol} missing required 'tick_size'")
        try:
            tick_size = float(p["tick_size"])
            if not math.isfinite(tick_size) or tick_size <= 0.0:
                raise ValueError()
        except (ValueError, TypeError):
            raise DeltaProductSpecificationError(
                f"Product {symbol} has invalid non-positive/non-finite 'tick_size': {p.get('tick_size')}"
            )

        # Minimum Order Size (must be >= 1, integer)
        if "min_order_size" not in p or p["min_order_size"] is None:
            raise DeltaProductSpecificationError(f"Product {symbol} missing required 'min_order_size'")
        try:
            min_order_size = int(p["min_order_size"])
            if min_order_size < 1:
                raise ValueError()
        except (ValueError, TypeError):
            raise DeltaProductSpecificationError(
                f"Product {symbol} has invalid 'min_order_size' (< 1): {p.get('min_order_size')}"
            )

        # Maximum Order Size (must be >= min_order_size, integer)
        if "max_order_size" not in p or p["max_order_size"] is None:
            raise DeltaProductSpecificationError(f"Product {symbol} missing required 'max_order_size'")
        try:
            max_order_size = int(p["max_order_size"])
            if max_order_size < min_order_size:
                raise ValueError()
        except (ValueError, TypeError):
            raise DeltaProductSpecificationError(
                f"Product {symbol} has invalid 'max_order_size': {p.get('max_order_size')}"
            )

        # Contract Type
        contract_type = p.get("contract_type")
        if not contract_type or not isinstance(contract_type, str):
            raise DeltaProductSpecificationError(f"Product {symbol} missing valid 'contract_type'")

        # Quoting & Settling Assets
        quoting_asset = (
            p.get("quoting_asset", {}).get("symbol")
            if isinstance(p.get("quoting_asset"), dict)
            else p.get("quoting_asset")
        )
        if not quoting_asset or not isinstance(quoting_asset, str):
            raise DeltaProductSpecificationError(f"Product {symbol} missing valid 'quoting_asset'")

        settling_asset = (
            p.get("settling_asset", {}).get("symbol")
            if isinstance(p.get("settling_asset"), dict)
            else p.get("settling_asset")
        )
        if not settling_asset or not isinstance(settling_asset, str):
            raise DeltaProductSpecificationError(f"Product {symbol} missing valid 'settling_asset'")

        # Trading Status
        trading_status = p.get("trading_status")
        if not trading_status or not isinstance(trading_status, str):
            raise DeltaProductSpecificationError(f"Product {symbol} missing valid 'trading_status'")

        # Fee Rates
        try:
            maker_fee = float(p.get("maker_commission_rate", 0.0002))
            taker_fee = float(p.get("taker_commission_rate", 0.0005))
            if not math.isfinite(maker_fee) or maker_fee < 0.0 or not math.isfinite(taker_fee) or taker_fee < 0.0:
                raise ValueError()
        except (ValueError, TypeError):
            raise DeltaProductSpecificationError(f"Product {symbol} has invalid fee rates")

        return DeltaProductSpecification(
            id=prod_id_int,
            symbol=symbol,
            contract_type=contract_type,
            quoting_asset=quoting_asset,
            settling_asset=settling_asset,
            contract_value=contract_value,
            tick_size=tick_size,
            min_order_size=min_order_size,
            max_order_size=max_order_size,
            trading_status=trading_status,
            maker_fee_rate=maker_fee,
            taker_fee_rate=taker_fee,
        )

    async def get_product_by_symbol(self, symbol: str = "PAXGUSDT") -> Optional[DeltaProductSpecification]:
        """Discover and validate product specification for target symbol."""
        products = await self.get_products()
        for p in products:
            if p.get("symbol") == symbol:
                return self._validate_product_specification(p)
        return None

    # Authenticated API Methods
    async def get_balances(self) -> List[Dict[str, Any]]:
        """Query account wallet balances from /v2/wallet/balances."""
        data = await self._request("GET", "/v2/wallet/balances", authenticated=True)
        return data.get("result", [])

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Query open active positions from /v2/positions."""
        data = await self._request("GET", "/v2/positions", authenticated=True)
        return data.get("result", [])

    async def get_orders(
        self,
        product_id: Optional[int] = None,
        state: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query open orders from /v2/orders."""
        params: Dict[str, Any] = {}
        if product_id is not None:
            params["product_id"] = product_id
        if state is not None:
            params["state"] = state
        data = await self._request("GET", "/v2/orders", params=params, authenticated=True)
        return data.get("result", [])

    async def get_order_by_client_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        """Find an order by client_order_id."""
        orders = await self.get_orders()
        for o in orders:
            if o.get("client_order_id") == client_order_id:
                return o
        return None

    async def place_order(self, request: DeltaOrderRequest) -> Dict[str, Any]:
        """Dispatch order intent to Delta Exchange.
        
        FAIL-CLOSED SAFETY GUARD:
        If environment is PRODUCTION and live_trading_enabled is False, this call
        is unconditionally blocked and raises DeltaLiveTradingDisabledError.
        """
        if self.config.environment == DeltaEnvironment.PRODUCTION:
            if not self.config.live_trading_enabled:
                raise DeltaLiveTradingDisabledError(
                    "Production live order placement strictly blocked: LIVE_TRADING_ENABLED is False"
                )

        payload = request.to_dict()
        logger.info(
            f"Dispatching Delta order: product_id={request.product_id}, "
            f"size={request.size}, side={request.side.value}, "
            f"order_type={request.order_type.value}, reduce_only={request.reduce_only}"
        )
        data = await self._request("POST", "/v2/orders", json_data=payload, authenticated=True)
        return data.get("result", {})

    async def cancel_order(self, order_id: int, product_id: int) -> Dict[str, Any]:
        """Cancel resting order via /v2/orders/cancel.
        
        FAIL-CLOSED SAFETY GUARD:
        If environment is PRODUCTION and live_trading_enabled is False, this call
        is unconditionally blocked and raises DeltaLiveTradingDisabledError.
        """
        if self.config.environment == DeltaEnvironment.PRODUCTION:
            if not self.config.live_trading_enabled:
                raise DeltaLiveTradingDisabledError(
                    "Production order cancellation strictly blocked: LIVE_TRADING_ENABLED is False"
                )

        payload = {"id": order_id, "product_id": product_id}
        data = await self._request("POST", "/v2/orders/cancel", json_data=payload, authenticated=True)
        return data.get("result", {})

    async def cancel_all_orders(self, product_id: Optional[int] = None) -> Dict[str, Any]:
        """Cancel all resting orders via /v2/orders/cancel_all.
        
        FAIL-CLOSED SAFETY GUARD:
        If environment is PRODUCTION and live_trading_enabled is False, this call
        is unconditionally blocked and raises DeltaLiveTradingDisabledError.
        """
        if self.config.environment == DeltaEnvironment.PRODUCTION:
            if not self.config.live_trading_enabled:
                raise DeltaLiveTradingDisabledError(
                    "Production order cancellation strictly blocked: LIVE_TRADING_ENABLED is False"
                )

        payload: Dict[str, Any] = {}
        if product_id is not None:
            payload["product_id"] = product_id
        data = await self._request("POST", "/v2/orders/cancel_all", json_data=payload, authenticated=True)
        return data.get("result", {})

