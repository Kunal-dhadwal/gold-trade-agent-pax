"""Deterministic bar-by-bar backtesting simulation engine."""

from datetime import datetime
from typing import Dict, List, Optional
import numpy as np
import pandas as pd
from src.core.constants import OrderDirection, ExitReason
from src.core.logger import logger
from src.backtest.execution import ExecutionModel
from src.backtest.metrics import MetricsEngine
from src.backtest.types import BacktestConfig, BacktestResult, TradeRecord
from src.strategy.base import BaseStrategy, StrategySignal


class BacktestEngine:
    """Deterministic event-driven bar-by-bar backtester for Gold."""

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()
        self.execution_model = ExecutionModel(self.config)

    def run(
        self,
        df: pd.DataFrame,
        strategy: BaseStrategy,
        instrument: str = "XAUUSD",
        timeframe: str = "1h",
    ) -> BacktestResult:
        """Execute deterministic backtest on the provided OHLCV dataset."""
        if df.empty or len(df) < 2:
            raise ValueError(f"Insufficient historical data ({len(df)} bars) for backtest.")

        logger.info(f"Starting deterministic backtest: {strategy.name} on {instrument} [{timeframe}] ({len(df)} bars)")

        # Generate signals deterministically
        signals_list = strategy.generate_signals(df)
        signals_by_ts: Dict[datetime, StrategySignal] = {s.timestamp: s for s in signals_list}

        cash = self.config.initial_capital
        equity = self.config.initial_capital
        current_position: Optional[Dict] = None

        trades: List[TradeRecord] = []
        equity_curve: List[Dict] = []
        trade_counter = 0

        # Bar-by-bar execution loop
        for i in range(len(df)):
            row = df.iloc[i]
            ts = row["timestamp"]
            open_p = float(row["open"])
            high_p = float(row["high"])
            low_p = float(row["low"])
            close_p = float(row["close"])

            # 1. Update active position excursions & check intra-bar exits
            if current_position is not None:
                current_position["holding_bars"] += 1
                pos_dir = current_position["direction"]
                entry_p = current_position["fill_entry"]
                sl_p = current_position["stop_loss"]
                tp_p = current_position["take_profit"]

                # Excursions
                if pos_dir == OrderDirection.LONG:
                    adverse = (entry_p - low_p) / entry_p
                    favorable = (high_p - entry_p) / entry_p
                else:
                    adverse = (high_p - entry_p) / entry_p
                    favorable = (entry_p - low_p) / entry_p

                current_position["max_mae"] = max(current_position["max_mae"], max(0.0, adverse))
                current_position["max_mfe"] = max(current_position["max_mfe"], max(0.0, favorable))

                # Check SL / TP hits
                sl_hit = (low_p <= sl_p) if pos_dir == OrderDirection.LONG else (high_p >= sl_p)
                tp_hit = (high_p >= tp_p) if pos_dir == OrderDirection.LONG else (low_p <= tp_p)

                exit_triggered = False
                exit_price_raw = 0.0
                exit_reason = ExitReason.STOP_LOSS

                # Conservative tie-breaking: If both SL and TP touched, assume SL hit first
                if sl_hit and tp_hit:
                    exit_triggered = True
                    exit_price_raw = sl_p
                    exit_reason = ExitReason.STOP_LOSS
                elif sl_hit:
                    exit_triggered = True
                    exit_price_raw = sl_p
                    exit_reason = ExitReason.STOP_LOSS
                elif tp_hit:
                    exit_triggered = True
                    exit_price_raw = tp_p
                    exit_reason = ExitReason.TAKE_PROFIT

                if exit_triggered:
                    # Calculate exit fill
                    fill_exit, slip_exit, spread_exit = self.execution_model.calculate_exit_fill(exit_price_raw, pos_dir)
                    exit_fee = self.execution_model.calculate_fee(fill_exit, current_position["size"])

                    # PnL
                    size = current_position["size"]
                    mult = self.config.contract_multiplier
                    if pos_dir == OrderDirection.LONG:
                        gross_pnl = (fill_exit - current_position["fill_entry"]) * size * mult
                    else:
                        gross_pnl = (current_position["fill_entry"] - fill_exit) * size * mult

                    total_trade_fees = current_position["entry_fee"] + exit_fee
                    total_trade_slip = (current_position["entry_slip"] + slip_exit) * size * mult
                    net_pnl = gross_pnl - total_trade_fees

                    cash += net_pnl
                    trade_counter += 1

                    # R-multiple
                    initial_risk_usd = current_position["initial_risk_usd"]
                    r_mult = (net_pnl / initial_risk_usd) if initial_risk_usd > 0 else 0.0
                    ret_pct = (net_pnl / current_position["allocated_equity"]) * 100.0

                    trade_rec = TradeRecord(
                        trade_number=trade_counter,
                        direction=pos_dir,
                        entry_time=current_position["entry_time"],
                        exit_time=ts,
                        entry_price=current_position["fill_entry"],
                        exit_price=fill_exit,
                        size=size,
                        initial_sl=sl_p,
                        initial_tp=tp_p,
                        gross_pnl=gross_pnl,
                        net_pnl=net_pnl,
                        return_pct=ret_pct,
                        r_multiple=r_mult,
                        fees=total_trade_fees,
                        slippage=total_trade_slip,
                        mae_pct=current_position["max_mae"] * 100.0,
                        mfe_pct=current_position["max_mfe"] * 100.0,
                        exit_reason=exit_reason,
                        holding_bars=current_position["holding_bars"],
                    )
                    trades.append(trade_rec)
                    current_position = None

            # 2. Check for new entry signal at bar close (if flat)
            if current_position is None and ts in signals_by_ts:
                sig = signals_by_ts[ts]
                # Position Sizing based on Dollar Risk:
                # Dollar Risk = Equity * risk_per_trade_pct
                dollar_risk = cash * (self.config.risk_per_trade_pct / 100.0)
                price_risk = abs(sig.entry_price - sig.stop_loss)
                if price_risk > 0:
                    calculated_size = dollar_risk / (price_risk * self.config.contract_multiplier)
                    pos_size = min(max(calculated_size, 0.01), self.config.max_position_size)

                    # Calculate entry fill
                    fill_entry, slip_entry, spread_entry = self.execution_model.calculate_entry_fill(sig.entry_price, sig.direction)
                    entry_fee = self.execution_model.calculate_fee(fill_entry, pos_size)

                    current_position = {
                        "direction": sig.direction,
                        "entry_time": ts,
                        "quote_entry": sig.entry_price,
                        "fill_entry": fill_entry,
                        "stop_loss": sig.stop_loss,
                        "take_profit": sig.take_profit,
                        "size": pos_size,
                        "entry_fee": entry_fee,
                        "entry_slip": slip_entry,
                        "allocated_equity": cash,
                        "initial_risk_usd": dollar_risk,
                        "max_mae": 0.0,
                        "max_mfe": 0.0,
                        "holding_bars": 0,
                    }

            # 3. Calculate current bar end equity
            unrealized_pnl = 0.0
            if current_position is not None:
                pos_dir = current_position["direction"]
                size = current_position["size"]
                mult = self.config.contract_multiplier
                if pos_dir == OrderDirection.LONG:
                    unrealized_pnl = (close_p - current_position["fill_entry"]) * size * mult
                else:
                    unrealized_pnl = (current_position["fill_entry"] - close_p) * size * mult

            equity = cash + unrealized_pnl
            equity_curve.append({
                "timestamp": ts.isoformat(),
                "cash": round(cash, 2),
                "equity": round(equity, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
            })

        # Close any lingering open position at the final bar close
        if current_position is not None:
            last_row = df.iloc[-1]
            pos_dir = current_position["direction"]
            fill_exit, slip_exit, _ = self.execution_model.calculate_exit_fill(float(last_row["close"]), pos_dir)
            exit_fee = self.execution_model.calculate_fee(fill_exit, current_position["size"])
            size = current_position["size"]
            mult = self.config.contract_multiplier

            gross_pnl = (fill_exit - current_position["fill_entry"]) * size * mult if pos_dir == OrderDirection.LONG else (current_position["fill_entry"] - fill_exit) * size * mult
            total_trade_fees = current_position["entry_fee"] + exit_fee
            total_trade_slip = (current_position["entry_slip"] + slip_exit) * size * mult
            net_pnl = gross_pnl - total_trade_fees
            cash += net_pnl
            trade_counter += 1

            trade_rec = TradeRecord(
                trade_number=trade_counter,
                direction=pos_dir,
                entry_time=current_position["entry_time"],
                exit_time=last_row["timestamp"],
                entry_price=current_position["fill_entry"],
                exit_price=fill_exit,
                size=size,
                initial_sl=current_position["stop_loss"],
                initial_tp=current_position["take_profit"],
                gross_pnl=gross_pnl,
                net_pnl=net_pnl,
                return_pct=(net_pnl / current_position["allocated_equity"]) * 100.0,
                r_multiple=(net_pnl / current_position["initial_risk_usd"]) if current_position["initial_risk_usd"] > 0 else 0.0,
                fees=total_trade_fees,
                slippage=total_trade_slip,
                mae_pct=current_position["max_mae"] * 100.0,
                mfe_pct=current_position["max_mfe"] * 100.0,
                exit_reason=ExitReason.EXPIRY,
                holding_bars=current_position["holding_bars"],
            )
            trades.append(trade_rec)

        eq_series = pd.Series([e["equity"] for e in equity_curve])
        metrics = MetricsEngine.calculate_metrics(
            trades=trades,
            equity_series=eq_series,
            initial_capital=self.config.initial_capital,
        )

        logger.info(f"Backtest finished: {len(trades)} trades | Net PnL: ${metrics.total_net_profit:.2f} | Sharpe: {metrics.sharpe_ratio:.2f} | MaxDD: {metrics.max_drawdown_pct:.2f}%")

        return BacktestResult(
            strategy_id=strategy.strategy_id,
            instrument=instrument,
            timeframe=timeframe,
            start_date=df["timestamp"].iloc[0].isoformat(),
            end_date=df["timestamp"].iloc[-1].isoformat(),
            metrics=metrics,
            trades=trades,
            equity_curve=equity_curve,
            parameters=strategy.parameters,
            execution_assumptions={
                "spread_usd": self.config.spread_usd,
                "slippage_usd": self.config.slippage_usd,
                "maker_fee_rate": self.config.maker_fee_rate,
                "taker_fee_rate": self.config.taker_fee_rate,
                "risk_per_trade_pct": self.config.risk_per_trade_pct,
                "contract_multiplier": self.config.contract_multiplier,
            },
        )
