"""Type definitions and contracts for Delta Exchange API v2 integration."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class DeltaEnvironment(str, Enum):
    """Execution environment targets for Delta Exchange."""
    TESTNET = "testnet"
    PRODUCTION = "production"
    MOCK = "mock"


@dataclass(frozen=True)
class DeltaConfig:
    """Configuration parameters for Delta Exchange connector."""
    environment: DeltaEnvironment = DeltaEnvironment.TESTNET
    api_key: str = ""
    api_secret: str = ""
    base_url: str = "https://testnet-api.delta.exchange"
    ws_url: str = "wss://testnet-socket.delta.exchange"
    timeout_seconds: float = 10.0
    rate_limit_per_second: float = 10.0
    clock_drift_max_ms: float = 1000.0  # Max allowable clock drift (< 1000ms SLA)
    heartbeat_interval_seconds: float = 30.0
    heartbeat_timeout_seconds: float = 10.0
    connection_timeout_seconds: float = 45.0
    live_trading_enabled: bool = False  # Hard-locked safety flag

    def __repr__(self) -> str:
        """Prevent sensitive API key and secret leakage in logs and repr."""
        masked_key = f"{self.api_key[:4]}...{self.api_key[-4:]}" if len(self.api_key) > 8 else "***"
        return (
            f"DeltaConfig(environment={self.environment.value}, "
            f"base_url='{self.base_url}', "
            f"ws_url='{self.ws_url}', "
            f"api_key='{masked_key}', "
            f"live_trading_enabled={self.live_trading_enabled})"
        )


class DeltaOrderType(str, Enum):
    """Supported order types on Delta Exchange."""
    LIMIT = "limit_order"
    MARKET = "market_order"
    STOP_MARKET = "stop_market_order"
    STOP_LIMIT = "stop_limit_order"


class DeltaOrderSide(str, Enum):
    """Order sides."""
    BUY = "buy"
    SELL = "sell"


class DeltaTimeInForce(str, Enum):
    """Time in force specifications."""
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


@dataclass
class DeltaOrderRequest:
    """Order dispatch request payload contract."""
    product_id: int
    size: int  # Lots (contracts)
    side: DeltaOrderSide
    order_type: DeltaOrderType
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: DeltaTimeInForce = DeltaTimeInForce.GTC
    reduce_only: bool = False
    client_order_id: Optional[str] = None
    # Decision A: native bracket parameters (provisional pending spike verification)
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    bracket_orders: Optional[List[Dict[str, Any]]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to Delta API v2 JSON payload format."""
        payload: Dict[str, Any] = {
            "product_id": self.product_id,
            "size": self.size,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "time_in_force": self.time_in_force.value,
            "reduce_only": self.reduce_only,
        }
        if self.limit_price is not None:
            payload["limit_price"] = str(self.limit_price)
        if self.stop_price is not None:
            payload["stop_price"] = str(self.stop_price)
        if self.client_order_id:
            payload["client_order_id"] = self.client_order_id
        if self.stop_loss_price is not None:
            payload["stop_loss_price"] = str(self.stop_loss_price)
        if self.take_profit_price is not None:
            payload["take_profit_price"] = str(self.take_profit_price)
        if self.bracket_orders:
            payload["bracket_orders"] = self.bracket_orders
        return payload


@dataclass
class DeltaProductSpecification:
    """Discovered product specification for an exchange instrument."""
    id: int
    symbol: str
    contract_type: str
    quoting_asset: str
    settling_asset: str
    contract_value: float  # Multiplier in troy ounces (e.g. 0.001 for PAXG)
    tick_size: float
    min_order_size: int  # Minimum contracts
    max_order_size: int  # Maximum contracts
    trading_status: str  # e.g. "operational", "halted"
    maker_fee_rate: float
    taker_fee_rate: float


class DeltaChannel(str, Enum):
    """Private and public WebSocket channel names."""
    ORDERS = "orders"
    POSITIONS = "positions"
    USER_TRADES = "user_trades"
    MARGINS = "margins"
    V2_TICKER = "v2/ticker"
    CANDLES = "candlestick"


# Custom Exception Hierarchy
class DeltaConnectorError(Exception):
    """Base exception for Delta Exchange connector."""
    pass


class DeltaProductSpecificationError(DeltaConnectorError):
    """Raised when an instrument specification from exchange is missing, invalid, or malformed."""
    pass


class DeltaAuthenticationError(DeltaConnectorError):
    """Raised when HMAC authentication fails."""
    pass


class DeltaClockDriftError(DeltaConnectorError):
    """Raised when client clock drifts beyond SLA threshold (<1000ms)."""
    pass


class DeltaRateLimitError(DeltaConnectorError):
    """Raised when exchange rate limit is breached (HTTP 429)."""
    pass


class DeltaOrderRejectedError(DeltaConnectorError):
    """Raised when exchange rejects order submission."""
    def __init__(self, message: str, code: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class DeltaNetworkError(DeltaConnectorError):
    """Raised on socket drops, transport timeouts, or connectivity failures."""
    pass


class DeltaLiveTradingDisabledError(DeltaConnectorError):
    """Raised when live trading action is attempted while disabled."""
    pass


class DeltaTransportOrTimeSyncUnavailableError(DeltaConnectorError):
    """Raised when in-band automated operations cannot proceed due to clock desync or exchange transport outage."""
    pass


class LeadershipLostError(DeltaConnectorError):
    """Raised when leadership lease is missing, expired, or invalid."""
    pass


class InterPositionEntryBlockedError(DeltaConnectorError):
    """Raised when a new entry order is attempted while an exit order from a prior position is active/unconfirmed."""
    pass


class IdempotencyConflictError(DeltaConnectorError):
    """Raised when a client_order_id is submitted with conflicting parameters."""
    pass


class DiscrepancyBlockedError(DeltaConnectorError):
    """Raised when a new entry order is blocked due to unresolved reconciliation discrepancies."""
    pass


class PositionBoundsExceededError(DeltaConnectorError):
    """Raised when a protective exit order quantity exceeds known position inventory."""
    pass


