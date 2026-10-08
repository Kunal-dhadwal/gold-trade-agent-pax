"""Central strategy registry."""

from typing import Any, Dict, List, Optional, Type
from src.strategy.base import BaseStrategy
from src.strategy.strategies.ema_cross import EMACrossoverStrategy
from src.strategy.strategies.atr_breakout import ATRBreakoutStrategy
from src.strategy.strategies.rsi_reversion import RSIMeanReversionStrategy


class StrategyRegistry:
    """Registry maintaining catalogue of available quantitative strategies."""

    _registry: Dict[str, Type[BaseStrategy]] = {
        EMACrossoverStrategy.strategy_id: EMACrossoverStrategy,
        ATRBreakoutStrategy.strategy_id: ATRBreakoutStrategy,
        RSIMeanReversionStrategy.strategy_id: RSIMeanReversionStrategy,
    }

    @classmethod
    def register(cls, strategy_cls: Type[BaseStrategy]) -> None:
        """Register a new strategy class."""
        cls._registry[strategy_cls.strategy_id] = strategy_cls

    @classmethod
    def get(cls, strategy_id: str, parameters: Optional[Dict[str, Any]] = None) -> BaseStrategy:
        """Instantiate a strategy by id."""
        if strategy_id not in cls._registry:
            raise KeyError(f"Strategy '{strategy_id}' not found. Available: {list(cls._registry.keys())}")
        return cls._registry[strategy_id](parameters)

    @classmethod
    def list_all(cls) -> List[Dict[str, Any]]:
        """Return list of all registered strategies with specifications."""
        result = []
        for strat_id, strat_cls in cls._registry.items():
            instance = strat_cls()
            result.append({
                "strategy_id": instance.strategy_id,
                "name": instance.name,
                "category": instance.category,
                "version_tag": instance.version_tag,
                "description": instance.description,
                "parameters": [p.model_dump() for p in instance.define_parameters()],
            })
        return result
