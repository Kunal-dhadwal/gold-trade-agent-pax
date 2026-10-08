"""Base strategy abstract interface and signal dataclass."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import pandas as pd
from src.core.constants import OrderDirection, MarketRegimeType
from src.strategy.parameters import ParameterSpec


@dataclass
class StrategySignal:
    timestamp: datetime
    direction: OrderDirection
    entry_price: float
    stop_loss: float
    take_profit: float
    suggested_size: float = 1.0
    risk_reward: float = 2.0
    regime: MarketRegimeType = MarketRegimeType.RANGING
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseStrategy(ABC):
    """Abstract base class for all deterministic quantitative strategies."""

    strategy_id: str = "base_strategy"
    name: str = "Base Strategy"
    category: str = "GENERIC"
    version_tag: str = "v1"
    description: str = ""

    def __init__(self, parameters: Optional[Dict[str, Any]] = None):
        self.param_specs: Dict[str, ParameterSpec] = {p.name: p for p in self.define_parameters()}
        self.parameters: Dict[str, Any] = {}

        # Set default parameters
        for name, spec in self.param_specs.items():
            self.parameters[name] = spec.default

        # Override with user inputs after validation
        if parameters:
            for k, v in parameters.items():
                if k in self.param_specs:
                    self.parameters[k] = self.param_specs[k].validate_value(v)
                else:
                    self.parameters[k] = v

    @abstractmethod
    def define_parameters(self) -> List[ParameterSpec]:
        """Define parameter schema, ranges, and defaults."""
        pass

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> List[StrategySignal]:
        """Compute trading signals strictly based on past and present bars."""
        pass

    def get_parameter(self, name: str, default: Any = None) -> Any:
        return self.parameters.get(name, default)
