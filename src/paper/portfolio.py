"""Single-Strategy Paper Portfolio and Equity Accounting for Phase 3."""

from datetime import datetime, timezone
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class SingleStrategyPortfolio:
    """Manages cash balance, equity, margin reservations, and drawdown calculations for a single paper strategy."""

    def __init__(self, account_id: str, initial_capital: float = 10000.0, leverage: float = 5.0):
        self.account_id = account_id
        self.initial_capital = float(initial_capital)
        self.cash_balance = float(initial_capital)
        self.peak_equity = float(initial_capital)
        self.starting_daily_equity = float(initial_capital)
        self.last_daily_reset_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.leverage = float(leverage)

        self.used_margin = 0.0
        self.realized_pnl = 0.0
        self.total_fees = 0.0
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.gross_profit = 0.0
        self.gross_loss = 0.0

    def check_daily_reset(self, current_dt: datetime, unrealized_pnl: float = 0.0) -> None:
        """Check and execute daily equity reset at 00:00:00 UTC boundary."""
        today_str = current_dt.strftime("%Y-%m-%d")
        if today_str != self.last_daily_reset_date:
            current_equity = self.get_equity(unrealized_pnl)
            self.starting_daily_equity = current_equity
            self.last_daily_reset_date = today_str
            logger.info(
                f"UTC Daily reset triggered for account {self.account_id}. "
                f"New starting daily equity: ${self.starting_daily_equity:.2f}"
            )

    def get_equity(self, unrealized_pnl: float = 0.0) -> float:
        """Calculate total account equity: Cash + Floating Unrealized PnL."""
        eq = self.cash_balance + unrealized_pnl
        if eq > self.peak_equity:
            self.peak_equity = eq
        return eq

    def get_drawdown_pct(self, unrealized_pnl: float = 0.0) -> float:
        """Calculate current drawdown percentage: (Peak - Equity) / Peak * 100."""
        equity = self.get_equity(unrealized_pnl)
        if self.peak_equity <= 0:
            return 0.0
        dd = ((self.peak_equity - equity) / self.peak_equity) * 100.0
        return max(0.0, dd)

    def get_max_drawdown_pct(self, unrealized_pnl: float = 0.0) -> float:
        """Alias for get_drawdown_pct."""
        return self.get_drawdown_pct(unrealized_pnl)

    def get_daily_loss_pct(self, unrealized_pnl: float = 0.0) -> float:
        """Calculate daily loss percentage: (StartingDailyEquity - Equity) / StartingDailyEquity * 100."""
        equity = self.get_equity(unrealized_pnl)
        if self.starting_daily_equity <= 0:
            return 0.0
        loss = ((self.starting_daily_equity - equity) / self.starting_daily_equity) * 100.0
        return max(0.0, loss)

    @property
    def margin_reserved(self) -> float:
        return self.used_margin

    @property
    def reserved_margin(self) -> float:
        return self.used_margin

    def get_available_margin(self, unrealized_pnl: float = 0.0) -> float:
        return max(0.0, self.get_equity(unrealized_pnl) - self.used_margin)

    def get_daily_pnl(self, unrealized_pnl: float = 0.0) -> float:
        return self.get_equity(unrealized_pnl) - self.starting_daily_equity


    def reserve_margin(self, notional: float) -> None:
        """Reserve collateral margin for a newly opened position."""
        req_margin = notional / self.leverage
        self.used_margin += req_margin

    def release_margin(self, notional: float) -> None:
        """Release collateral margin when a position is closed."""
        req_margin = notional / self.leverage
        self.used_margin = max(0.0, self.used_margin - req_margin)

    def settle_trade(self, net_pnl: float, fees: float) -> None:
        """Settle a closed trade: update cash balance, metrics, and trade counters."""
        self.cash_balance += net_pnl
        self.realized_pnl += net_pnl
        self.total_fees += fees
        self.total_trades += 1

        if net_pnl > 0:
            self.winning_trades += 1
            self.gross_profit += net_pnl
        else:
            self.losing_trades += 1
            self.gross_loss += abs(net_pnl)

        # Update peak equity with new settled cash
        if self.cash_balance > self.peak_equity:
            self.peak_equity = self.cash_balance

    def to_dict(self, unrealized_pnl: float = 0.0) -> Dict[str, Any]:
        """Return diagnostic snapshot dictionary."""
        equity = self.get_equity(unrealized_pnl)
        return {
            "account_id": self.account_id,
            "cash_balance": self.cash_balance,
            "equity": equity,
            "peak_equity": self.peak_equity,
            "starting_daily_equity": self.starting_daily_equity,
            "used_margin": self.used_margin,
            "free_margin": max(0.0, equity - self.used_margin),
            "drawdown_pct": self.get_drawdown_pct(unrealized_pnl),
            "daily_loss_pct": self.get_daily_loss_pct(unrealized_pnl),
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate_pct": (self.winning_trades / self.total_trades * 100.0) if self.total_trades > 0 else 0.0,
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "profit_factor": (self.gross_profit / self.gross_loss) if self.gross_loss > 0 else 999.0,
        }
