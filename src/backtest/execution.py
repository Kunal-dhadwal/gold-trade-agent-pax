"""Execution cost modeling: spreads, adverse slippage, and exchange fees."""

from typing import Tuple
from src.core.constants import OrderDirection
from src.backtest.types import BacktestConfig


class ExecutionModel:
    """Computes realistic execution fills with spread, slippage, and taker fees."""

    def __init__(self, config: BacktestConfig):
        self.config = config

    def calculate_entry_fill(self, quote_price: float, direction: OrderDirection) -> Tuple[float, float, float]:
        """Return (fill_price, slippage_cost_usd, half_spread_cost_usd)."""
        half_spread = self.config.spread_usd / 2.0
        slippage = self.config.slippage_usd

        if direction == OrderDirection.LONG:
            # Pay half-spread + adverse slippage upward
            fill_price = quote_price + half_spread + slippage
        else:
            # Sell into bid: half-spread + adverse slippage downward
            fill_price = quote_price - half_spread - slippage

        return fill_price, slippage, half_spread

    def calculate_exit_fill(self, quote_price: float, direction: OrderDirection) -> Tuple[float, float, float]:
        """Return (fill_price, slippage_cost_usd, half_spread_cost_usd)."""
        half_spread = self.config.spread_usd / 2.0
        slippage = self.config.slippage_usd

        # Exiting long means selling into the bid
        if direction == OrderDirection.LONG:
            fill_price = quote_price - half_spread - slippage
        else:
            # Exiting short means buying from the ask
            fill_price = quote_price + half_spread + slippage

        return fill_price, slippage, half_spread

    def calculate_fee(self, fill_price: float, size: float, is_maker: bool = False) -> float:
        """Calculate transaction fee for a fill."""
        fee_rate = self.config.maker_fee_rate if is_maker else self.config.taker_fee_rate
        notional = fill_price * size * self.config.contract_multiplier
        return notional * fee_rate
