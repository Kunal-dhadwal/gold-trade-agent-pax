"""Backtesting data structures, configurations, and results."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from src.core.constants import OrderDirection, ExitReason


@dataclass
class BacktestConfig:
    initial_capital: float = 10000.0
    spread_usd: float = 0.30  # Half spread is 0.15
    slippage_usd: float = 0.15  # Adverse slippage
    maker_fee_rate: float = 0.0002  # 0.02%
    taker_fee_rate: float = 0.0005  # 0.05%
    risk_per_trade_pct: float = 1.0  # 1% equity risked per trade
    contract_multiplier: float = 1.0  # 1.0 for spot oz, 100.0 for futures contract
    max_position_size: float = 100.0  # Maximum ounces/contracts
    execution_on_next_bar_open: bool = False  # False = close of signal bar, True = open of next bar


@dataclass
class TradeRecord:
    trade_number: int
    direction: OrderDirection
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    size: float
    initial_sl: float
    initial_tp: float
    gross_pnl: float
    net_pnl: float
    return_pct: float
    r_multiple: float
    fees: float
    slippage: float
    mae_pct: float
    mfe_pct: float
    exit_reason: ExitReason
    holding_bars: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trade_number": self.trade_number,
            "direction": self.direction.value,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat(),
            "entry_price": round(self.entry_price, 3),
            "exit_price": round(self.exit_price, 3),
            "size": round(self.size, 4),
            "initial_sl": round(self.initial_sl, 3),
            "initial_tp": round(self.initial_tp, 3),
            "gross_pnl": round(self.gross_pnl, 2),
            "net_pnl": round(self.net_pnl, 2),
            "return_pct": round(self.return_pct, 4),
            "r_multiple": round(self.r_multiple, 3),
            "fees": round(self.fees, 3),
            "slippage": round(self.slippage, 3),
            "mae_pct": round(self.mae_pct, 4),
            "mfe_pct": round(self.mfe_pct, 4),
            "exit_reason": self.exit_reason.value,
            "holding_bars": self.holding_bars,
        }


@dataclass
class BacktestMetrics:
    initial_capital: float
    final_equity: float
    total_net_profit: float
    total_return_pct: float
    cagr_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    profit_factor: float
    win_rate_pct: float
    max_drawdown_pct: float
    max_drawdown_usd: float
    max_drawdown_duration_bars: int
    recovery_factor: float
    expectancy_usd: float
    average_r: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    max_consecutive_losses: int
    max_adverse_excursion_avg: float
    max_favorable_excursion_avg: float
    total_fees_paid: float
    total_slippage_cost: float

    def to_dict(self) -> Dict[str, Any]:
        return {k: round(v, 4) if isinstance(v, float) else v for k, v in self.__dict__.items()}


@dataclass
class BacktestResult:
    strategy_id: str
    instrument: str
    timeframe: str
    start_date: str
    end_date: str
    metrics: BacktestMetrics
    trades: List[TradeRecord] = field(default_factory=list)
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    execution_assumptions: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "instrument": self.instrument,
            "timeframe": self.timeframe,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "metrics": self.metrics.to_dict(),
            "trades_count": len(self.trades),
            "parameters": self.parameters,
            "execution_assumptions": self.execution_assumptions,
            "trades": [t.to_dict() for t in self.trades],
            "equity_curve": self.equity_curve,
        }
