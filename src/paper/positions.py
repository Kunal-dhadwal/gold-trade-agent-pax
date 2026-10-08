"""In-memory position tracking and excursion monitoring for paper trading."""

from datetime import datetime, timezone
from typing import Optional, Tuple
from src.paper.specifications import InstrumentSpecification


class PositionTracker:
    """Manages an active open paper position, excursion monitoring, and bracket SL/TP checks."""

    def __init__(
        self,
        symbol: str,
        direction: str,
        quantity: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        allocated_margin: float,
        entry_fee: float = 0.0,
        entry_slippage: float = 0.0,
        opened_at: Optional[datetime] = None,
    ):
        self.symbol = symbol.strip().upper()
        self.direction = direction.strip().upper()  # LONG or SHORT
        self.quantity = float(quantity)
        self.entry_price = float(entry_price)
        self.current_mark_price = float(entry_price)
        self.stop_loss = float(stop_loss)
        self.take_profit = float(take_profit)
        self.allocated_margin = float(allocated_margin)
        self.total_fees = float(entry_fee)
        self.total_slippage = float(entry_slippage)
        self.opened_at = opened_at or datetime.now(timezone.utc)
        self.holding_bars = 0
        self.max_mae_pct = 0.0
        self.max_mfe_pct = 0.0
        self.state = "OPEN"

    @property
    def max_mae_usd(self) -> float:
        return (self.max_mae_pct / 100.0) * self.entry_price * self.quantity * 0.001

    @property
    def max_mfe_usd(self) -> float:
        return (self.max_mfe_pct / 100.0) * self.entry_price * self.quantity * 0.001


    def update_mark(self, mark_price: float, high: float, low: float) -> None:
        """Update current mark price, holding bars, and excursion metrics."""
        self.current_mark_price = float(mark_price)
        self.holding_bars += 1

        if self.entry_price > 0:
            if self.direction == "LONG":
                adverse = (self.entry_price - low) / self.entry_price
                favorable = (high - self.entry_price) / self.entry_price
            else:
                adverse = (high - self.entry_price) / self.entry_price
                favorable = (self.entry_price - low) / self.entry_price

            self.max_mae_pct = max(self.max_mae_pct, max(0.0, adverse) * 100.0)
            self.max_mfe_pct = max(self.max_mfe_pct, max(0.0, favorable) * 100.0)

    def calculate_unrealized_pnl(self, spec: InstrumentSpecification) -> float:
        """Calculate floating unrealized PnL: (Mark - Entry) * Qty * ContractSize."""
        mult = spec.contract_size
        if self.direction == "LONG":
            return (self.current_mark_price - self.entry_price) * self.quantity * mult
        else:
            return (self.entry_price - self.current_mark_price) * self.quantity * mult

    def check_bracket_exit(self, high: float, low: float) -> Tuple[bool, Optional[str], float]:
        """Evaluate intra-bar Stop Loss and Take Profit levels with conservative tie-breaking.

        Returns (exit_triggered, exit_reason, raw_exit_price).
        Invariant: If both SL and TP are touched in the same bar, Stop Loss is assumed first.
        """
        if self.direction == "LONG":
            sl_hit = low <= self.stop_loss
            tp_hit = high >= self.take_profit
        else:
            sl_hit = high >= self.stop_loss
            tp_hit = low <= self.take_profit

        # Conservative Invariant: If both touched, SL triggers first
        if sl_hit and tp_hit:
            return True, "STOP_LOSS", self.stop_loss
        elif sl_hit:
            return True, "STOP_LOSS", self.stop_loss
        elif tp_hit:
            return True, "TAKE_PROFIT", self.take_profit

        return False, None, 0.0
