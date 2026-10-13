"""Simulated Delta Exchange Server for offline unit, integration, and staging testing."""

import asyncio
from datetime import datetime, timezone
import json
import time
from typing import Any, Dict, List, Optional
import httpx
from src.execution.delta.signer import DeltaRequestSigner


class MockDeltaExchangeServer:
    """In-memory mock server simulating Delta Exchange REST v2 API and behavior.
    
    Provides:
    - Verification of HMAC-SHA256 signatures.
    - Simulation of clock drift and time offsets.
    - Simulation of rate limiting (HTTP 429).
    - Simulation of order execution, partial fills, cancellations, and reduce_only rejections.
    - Full offline compatibility without external network dependencies.
    """

    def __init__(
        self,
        api_key: str = "mock_api_key",
        api_secret: str = "mock_api_secret_32_bytes_long_key",
        enforce_signatures: bool = True,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.enforce_signatures = enforce_signatures
        self.signer = DeltaRequestSigner(api_key, api_secret)

        # Simulation toggles
        self.clock_drift_offset_ms: float = 0.0
        self.rate_limit_active: bool = False
        self.rate_limit_retry_after: float = 2.0
        self.force_auth_failure: bool = False
        self.force_network_timeout: bool = False

        # In-memory storage
        self.orders: Dict[int, Dict[str, Any]] = {}
        self.positions: Dict[int, Dict[str, Any]] = {}
        self.balances: Dict[str, float] = {"USDT": 10000.0}
        self._next_order_id = 1001

        # Preloaded product definition for PAXGUSDT
        self.products: List[Dict[str, Any]] = [
            {
                "id": 1234,
                "symbol": "PAXGUSDT",
                "contract_type": "perpetual_futures",
                "quoting_asset": {"symbol": "USDT"},
                "settling_asset": {"symbol": "USDT"},
                "contract_value": "0.001",
                "tick_size": "0.01",
                "min_order_size": 1,
                "max_order_size": 10000,
                "trading_status": "operational",
                "maker_commission_rate": "0.0002",
                "taker_commission_rate": "0.0005",
            },
            {
                "id": 5678,
                "symbol": "BTCUSDT",
                "contract_type": "perpetual_futures",
                "quoting_asset": {"symbol": "USDT"},
                "settling_asset": {"symbol": "USDT"},
                "contract_value": "0.001",
                "tick_size": "0.1",
                "min_order_size": 1,
                "max_order_size": 50000,
                "trading_status": "operational",
                "maker_commission_rate": "0.0002",
                "taker_commission_rate": "0.0005",
            },
        ]

    def get_server_time_ms(self) -> int:
        """Calculate simulated server time with applied drift offset."""
        return int(time.time() * 1000 + self.clock_drift_offset_ms)

    def _verify_request_auth(self, request: httpx.Request) -> bool:
        """Verify HMAC headers on authenticated requests."""
        if not self.enforce_signatures:
            return True
        if self.force_auth_failure:
            return False

        api_key = request.headers.get("api-key")
        timestamp_str = request.headers.get("timestamp")
        signature = request.headers.get("signature")

        if not (api_key and timestamp_str and signature):
            return False
        if api_key != self.api_key:
            return False

        try:
            ts = int(timestamp_str)
        except ValueError:
            return False

        # Verify timestamp drift against exchange server window (Delta allows +-5000ms)
        server_ms = self.get_server_time_ms()
        if abs(server_ms - ts) > 5000:
            return False

        path = request.url.path
        method = request.method.upper()
        # Query string or body
        query_or_body = ""
        if request.url.query:
            query_or_body = "?" + request.url.query.decode("ascii")
        elif request.content:
            query_or_body = request.content.decode("utf-8")

        return self.signer.verify_rest_signature(
            signature=signature,
            method=method,
            timestamp_ms=ts,
            path=path,
            query_or_body=query_or_body,
        )

    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        """ASGI / httpx mock transport handler."""
        if self.force_network_timeout:
            await asyncio.sleep(0.5)
            raise httpx.TimeoutException("Simulated mock exchange timeout")

        if self.rate_limit_active:
            return httpx.Response(
                status_code=429,
                headers={"Retry-After": str(self.rate_limit_retry_after)},
                json={"error": {"code": "RateLimitExceeded", "message": "Too many requests"}},
            )

        path = request.url.path
        method = request.method.upper()

        # Public endpoints
        if path == "/v2/time" and method == "GET":
            return httpx.Response(
                status_code=200,
                json={"result": {"time": self.get_server_time_ms()}},
            )

        if path == "/v2/products" and method == "GET":
            return httpx.Response(
                status_code=200,
                json={"result": self.products},
            )

        # Authenticated endpoints check
        if not self._verify_request_auth(request):
            return httpx.Response(
                status_code=401,
                json={"error": {"code": "AuthenticationFailed", "message": "Invalid API key or signature"}},
            )

        if path == "/v2/wallet/balances" and method == "GET":
            balance_results = [
                {"asset_symbol": asset, "balance": str(bal), "available_balance": str(bal)}
                for asset, bal in self.balances.items()
            ]
            return httpx.Response(status_code=200, json={"result": balance_results})

        if path == "/v2/positions" and method == "GET":
            pos_results = []
            for pid, pos in self.positions.items():
                p_copy = dict(pos)
                if "product_id" not in p_copy:
                    p_copy["product_id"] = pid
                pos_results.append(p_copy)
            return httpx.Response(status_code=200, json={"result": pos_results})

        if path == "/v2/orders" and method == "GET":
            orders_list = list(self.orders.values())
            state_param = request.url.params.get("state")
            if state_param:
                orders_list = [o for o in orders_list if o.get("state") == state_param]
            product_id_param = request.url.params.get("product_id")
            if product_id_param:
                orders_list = [o for o in orders_list if str(o.get("product_id")) == str(product_id_param)]
            return httpx.Response(status_code=200, json={"result": orders_list})

        if path == "/v2/orders" and method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            product_id = body.get("product_id")
            size = body.get("size", 0)
            side = body.get("side")
            order_type = body.get("order_type")
            reduce_only = body.get("reduce_only", False)
            client_order_id = body.get("client_order_id")

            # Check reduce_only invariant
            if reduce_only:
                current_pos = self.positions.get(product_id, {}).get("size", 0)
                if current_pos == 0:
                    return httpx.Response(
                        status_code=400,
                        json={
                            "error": {
                                "code": "ReduceOnlyExceedsPosition",
                                "message": "Reduce-only order exceeds existing position",
                            }
                        },
                    )

            order_id = self._next_order_id
            self._next_order_id += 1

            new_order = {
                "id": order_id,
                "product_id": product_id,
                "size": size,
                "unfilled_size": size,
                "side": side,
                "order_type": order_type,
                "state": "open",
                "client_order_id": client_order_id,
                "reduce_only": reduce_only,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self.orders[order_id] = new_order
            return httpx.Response(status_code=200, json={"result": new_order})

        if path == "/v2/orders/cancel" and method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            order_id = body.get("id")
            if order_id in self.orders:
                if self.orders[order_id].get("state") == "filled":
                    return httpx.Response(
                        status_code=400,
                        json={"error": {"code": "OrderAlreadyFilled", "message": f"Order {order_id} cannot be cancelled because it is filled"}},
                    )
                self.orders[order_id]["state"] = "cancelled"
                return httpx.Response(status_code=200, json={"result": self.orders[order_id]})
            return httpx.Response(
                status_code=404,
                json={"error": {"code": "OrderNotFound", "message": f"Order {order_id} not found"}},
            )

        if path == "/v2/orders/cancel_all" and method == "POST":
            for o in self.orders.values():
                if o.get("state") == "open":
                    o["state"] = "cancelled"
            return httpx.Response(status_code=200, json={"result": {"success": True}})

        return httpx.Response(status_code=404, json={"error": "Not Found"})

    def create_mock_transport(self) -> httpx.MockTransport:
        """Create httpx.MockTransport routing directly to this mock server."""
        return httpx.MockTransport(self.handle_request)
