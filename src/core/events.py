"""Domain event models for decoupled system communication."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import uuid
from src.core.constants import OrderDirection, OrderType, ExitReason, MarketRegimeType


@dataclass(frozen=True)
class BarEvent:
    instrument: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class SignalEvent:
    strategy_id: str
    version_tag: str
    instrument: str
    timestamp: datetime
    direction: OrderDirection
    suggested_entry: float
    suggested_sl: float
    suggested_tp: float
    suggested_size: float
    risk_reward_ratio: float
    regime: MarketRegimeType
    metadata: Dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class RiskCheckResult:
    passed: bool
    reason: str
    approved_size: float
    approved_sl: float
    approved_tp: float
    risk_metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderEvent:
    signal_id: str
    strategy_id: str
    instrument: str
    direction: OrderDirection
    order_type: OrderType
    price: float
    size: float
    stop_loss: float
    take_profit: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    order_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class FillEvent:
    order_id: str
    signal_id: str
    instrument: str
    direction: OrderDirection
    fill_price: float
    fill_size: float
    slippage: float
    fee: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fill_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True)
class SystemEvent:
    component: str
    event_name: str
    status: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
