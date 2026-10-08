"""Quantitative performance metrics calculation engine."""

from typing import List, Tuple
import numpy as np
import pandas as pd
from src.backtest.types import BacktestMetrics, TradeRecord


class MetricsEngine:
    """Calculates institutional quantitative metrics from backtest equity and trade logs."""

    @classmethod
    def calculate_metrics(
        cls,
        trades: List[TradeRecord],
        equity_series: pd.Series,
        initial_capital: float,
        timeframe_minutes: int = 60,
    ) -> BacktestMetrics:
        """Calculate complete performance metrics suite."""
        final_equity = float(equity_series.iloc[-1]) if not equity_series.empty else initial_capital
        net_profit = final_equity - initial_capital
        total_return_pct = (net_profit / initial_capital) * 100.0

        total_trades = len(trades)
        if total_trades == 0:
            return BacktestMetrics(
                initial_capital=initial_capital,
                final_equity=final_equity,
                total_net_profit=0.0,
                total_return_pct=0.0,
                cagr_pct=0.0,
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                profit_factor=0.0,
                win_rate_pct=0.0,
                max_drawdown_pct=0.0,
                max_drawdown_usd=0.0,
                max_drawdown_duration_bars=0,
                recovery_factor=0.0,
                expectancy_usd=0.0,
                average_r=0.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                max_consecutive_losses=0,
                max_adverse_excursion_avg=0.0,
                max_favorable_excursion_avg=0.0,
                total_fees_paid=0.0,
                total_slippage_cost=0.0,
            )

        # Trade metrics
        winning_trades = [t for t in trades if t.net_pnl > 0]
        losing_trades = [t for t in trades if t.net_pnl <= 0]
        win_count = len(winning_trades)
        loss_count = len(losing_trades)
        win_rate = (win_count / total_trades) * 100.0

        gross_profit = sum(t.net_pnl for t in winning_trades)
        gross_loss = abs(sum(t.net_pnl for t in losing_trades))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

        # Average R and Expectancy
        r_multiples = [t.r_multiple for t in trades]
        avg_r = float(np.mean(r_multiples)) if r_multiples else 0.0
        avg_win = float(np.mean([t.net_pnl for t in winning_trades])) if winning_trades else 0.0
        avg_loss = float(np.mean([abs(t.net_pnl) for t in losing_trades])) if losing_trades else 0.0
        expectancy_usd = ((win_count / total_trades) * avg_win) - ((loss_count / total_trades) * avg_loss)

        # Consecutive losses
        max_consecutive_losses = 0
        current_streak = 0
        for t in trades:
            if t.net_pnl <= 0:
                current_streak += 1
                max_consecutive_losses = max(max_consecutive_losses, current_streak)
            else:
                current_streak = 0

        # Drawdown calculation
        peaks = equity_series.cummax()
        drawdowns_usd = peaks - equity_series
        drawdowns_pct = (drawdowns_usd / peaks) * 100.0

        max_dd_pct = float(drawdowns_pct.max()) if not drawdowns_pct.empty else 0.0
        max_dd_usd = float(drawdowns_usd.max()) if not drawdowns_usd.empty else 0.0

        # Drawdown duration (bars)
        dd_duration_bars = 0
        curr_dd_bars = 0
        for dd in drawdowns_usd:
            if dd > 0:
                curr_dd_bars += 1
                dd_duration_bars = max(dd_duration_bars, curr_dd_bars)
            else:
                curr_dd_bars = 0

        recovery_factor = (net_profit / max_dd_usd) if max_dd_usd > 0 else (99.0 if net_profit > 0 else 0.0)

        # Excursions
        avg_mae = float(np.mean([t.mae_pct for t in trades])) if trades else 0.0
        avg_mfe = float(np.mean([t.mfe_pct for t in trades])) if trades else 0.0
        total_fees = sum(t.fees for t in trades)
        total_slippage = sum(t.slippage for t in trades)

        # Period returns and Sharpe / Sortino
        returns = equity_series.pct_change().dropna()
        bars_per_day = max(1, int(1440 / max(timeframe_minutes, 1)))
        annual_factor = np.sqrt(252 * bars_per_day)

        if len(returns) > 1 and returns.std() > 0:
            sharpe = float((returns.mean() / returns.std()) * annual_factor)
            downside_returns = returns[returns < 0]
            downside_std = downside_returns.std() if len(downside_returns) > 1 else 0.0
            sortino = float((returns.mean() / downside_std) * annual_factor) if downside_std > 0 else sharpe
        else:
            sharpe = 0.0
            sortino = 0.0

        # Approximate CAGR
        total_bars = len(equity_series)
        days = max((total_bars / bars_per_day), 1.0)
        cagr = (((final_equity / initial_capital) ** (365.25 / days)) - 1.0) * 100.0 if final_equity > 0 else -100.0

        return BacktestMetrics(
            initial_capital=initial_capital,
            final_equity=final_equity,
            total_net_profit=net_profit,
            total_return_pct=total_return_pct,
            cagr_pct=cagr,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            profit_factor=profit_factor,
            win_rate_pct=win_rate,
            max_drawdown_pct=max_dd_pct,
            max_drawdown_usd=max_dd_usd,
            max_drawdown_duration_bars=dd_duration_bars,
            recovery_factor=recovery_factor,
            expectancy_usd=expectancy_usd,
            average_r=avg_r,
            total_trades=total_trades,
            winning_trades=win_count,
            losing_trades=loss_count,
            max_consecutive_losses=max_consecutive_losses,
            max_adverse_excursion_avg=avg_mae,
            max_favorable_excursion_avg=avg_mfe,
            total_fees_paid=total_fees,
            total_slippage_cost=total_slippage,
        )
