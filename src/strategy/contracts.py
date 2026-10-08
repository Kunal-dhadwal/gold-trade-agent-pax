"""Machine-readable strategy contract and AST schema for AI strategy proposals."""

from enum import Enum
from typing import Dict, List, Literal, Optional, Union
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, model_validator


class TradeDirection(str, Enum):
    """Explicit trade direction for a strategy proposal."""
    LONG = "LONG"
    SHORT = "SHORT"


class InstrumentEnum(str, Enum):
    XAUUSD = "XAUUSD"
    GC_F = "GC=F"
    PAXGUSDT = "PAXGUSDT"


class TimeframeEnum(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class DataSourceEnum(str, Enum):
    DUKASCOPY = "DUKASCOPY"
    YAHOO = "YAHOO"
    DELTA_EXCHANGE = "DELTA_EXCHANGE"


class RegimeEnum(str, Enum):
    TRENDING_BULL = "TRENDING_BULL"
    TRENDING_BEAR = "TRENDING_BEAR"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    BREAKOUT = "BREAKOUT"


class IndicatorNameEnum(str, Enum):
    EMA = "EMA"
    SMA = "SMA"
    RSI = "RSI"
    MACD = "MACD"
    ATR = "ATR"
    BOLLINGER = "BOLLINGER"
    ADX = "ADX"
    DONCHIAN = "DONCHIAN"


class IndicatorSpec(BaseModel):
    """Specification of an indicator required by the strategy."""
    alias: str = Field(..., pattern=r"^[a-z0-9_]{2,32}$")
    name: IndicatorNameEnum
    inputs: Dict[str, Union[int, float, str]]


class ASTParameterSpec(BaseModel):
    """Specification of an optimizable numerical parameter."""
    type: Literal["INT", "FLOAT"]
    default: float
    min_value: float
    max_value: float
    step: float

    @model_validator(mode="after")
    def validate_bounds(self) -> "ASTParameterSpec":
        if not (self.min_value <= self.default <= self.max_value):
            raise ValueError(f"Default {self.default} out of bounds [{self.min_value}, {self.max_value}]")
        if self.step <= 0:
            raise ValueError("Step must be positive")
        return self


class ValueRefType(str, Enum):
    PRICE = "PRICE"
    INDICATOR = "INDICATOR"
    CONSTANT = "CONSTANT"


class ValueRef(BaseModel):
    """Reference to a historical or current price, indicator series, or constant."""
    type: ValueRefType
    name: Optional[str] = Field(None, pattern=r"^[a-z0-9_]+$")
    value: Optional[float] = None
    lag: int = Field(default=0, ge=0, le=20)

    @model_validator(mode="after")
    def validate_ref(self) -> "ValueRef":
        if self.type in (ValueRefType.PRICE, ValueRefType.INDICATOR):
            if not self.name:
                raise ValueError(f"Field 'name' is required when type is {self.type.value}")
            if self.type == ValueRefType.PRICE and self.name not in ("open", "high", "low", "close", "volume"):
                raise ValueError(f"Invalid price field '{self.name}'. Must be open, high, low, close, volume.")
        elif self.type == ValueRefType.CONSTANT:
            if self.value is None:
                raise ValueError("Field 'value' is required when type is CONSTANT")
            if self.lag != 0:
                raise ValueError("Constants cannot have a non-zero lag")
        return self


class ConditionComparison(BaseModel):
    """Atomic comparison between two ValueRef elements."""
    left: ValueRef
    comparator: Literal[">", "<", ">=", "<=", "==", "CROSS_OVER", "CROSS_UNDER"]
    right: ValueRef


class ConditionNode(BaseModel):
    """Boolean composition of conditions."""
    operator: Literal["AND", "OR"]
    conditions: List[ConditionComparison] = Field(..., min_length=1, max_length=5)


class StopLossSpec(BaseModel):
    """Stop-loss placement rule."""
    type: Literal["ATR_MULTIPLE", "FIXED_POINTS", "TRAILING_ATR"]
    multiplier_or_value: float = Field(..., ge=0.5, le=100.0)


class TakeProfitSpec(BaseModel):
    """Take-profit placement rule."""
    type: Literal["ATR_MULTIPLE", "FIXED_POINTS", "RISK_REWARD_RATIO"]
    multiplier_or_value: float = Field(..., ge=0.75, le=200.0)


class ExecutionConstraints(BaseModel):
    """Execution bounds for signal generation."""
    max_holding_bars: int = Field(default=100, ge=1, le=500)
    allowed_sessions: List[Literal["LONDON", "NEW_YORK", "ASIAN", "ALL"]] = Field(
        default=["ALL"]
    )


class StrategyProposal(BaseModel):
    """Machine-readable strategy contract emitted by AI and validated deterministically."""
    strategy_id: UUID = Field(default_factory=uuid4)
    name: str = Field(..., pattern=r"^[A-Z0-9_]{3,32}$")
    hypothesis: str = Field(..., min_length=20, max_length=500)
    direction: TradeDirection
    instrument: InstrumentEnum
    timeframe: TimeframeEnum
    data_source: DataSourceEnum
    target_regimes: List[RegimeEnum] = Field(..., min_length=1)
    indicators: List[IndicatorSpec] = Field(..., min_length=1, max_length=6)
    parameters: Dict[str, ASTParameterSpec] = Field(default_factory=dict)
    entry_rules: ConditionNode
    exit_rules: ConditionNode
    stop_loss: StopLossSpec
    take_profit: TakeProfitSpec
    constraints: ExecutionConstraints = Field(default_factory=ExecutionConstraints)
    rationale: str = Field(..., min_length=20)
    generated_by: str = Field(default="Agent-3-StrategyResearcher")

    @model_validator(mode="after")
    def validate_risk_reward_gate(self) -> "StrategyProposal":
        """Verify institutional Risk:Reward gate (minimum 1.5 R:R)."""
        sl_val = self.stop_loss.multiplier_or_value
        tp_val = self.take_profit.multiplier_or_value

        # If both are of the same relative scale (e.g. ATR_MULTIPLE or FIXED_POINTS)
        if self.stop_loss.type == self.take_profit.type and sl_val > 0:
            rr = tp_val / sl_val
            if rr < 1.5:
                raise ValueError(
                    f"Risk:Reward ratio {rr:.2f} violates institutional minimum of 1.50 "
                    f"(TP: {tp_val}, SL: {sl_val})"
                )
        elif self.take_profit.type == "RISK_REWARD_RATIO":
            if tp_val < 1.5:
                raise ValueError(
                    f"Take-profit RISK_REWARD_RATIO {tp_val:.2f} violates minimum 1.50"
                )

        return self

