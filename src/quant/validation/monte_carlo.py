"""Deterministic Monte Carlo simulation and stress testing engine."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import numpy as np

from src.backtest.types import TradeRecord
from src.quant.validation.config import DEFAULT_VALIDATION_CONFIG, ValidationThresholdsConfig


@dataclass
class MonteCarloResultData:
    """Monte Carlo robustness metrics container."""
    iterations: int
    median_drawdown_pct: float
    p95_drawdown_pct: float
    worst_case_drawdown_pct: float
    probability_of_ruin: float
    p95_losing_streak: int
    stress_profit_factor: float
    param_degradation_pct: float
    passed: bool
    rejection_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iterations": self.iterations,
            "median_drawdown_pct": round(self.median_drawdown_pct, 4),
            "p95_drawdown_pct": round(self.p95_drawdown_pct, 4),
            "worst_case_drawdown_pct": round(self.worst_case_drawdown_pct, 4),
            "probability_of_ruin": round(self.probability_of_ruin, 4),
            "p95_losing_streak": self.p95_losing_streak,
            "stress_profit_factor": round(self.stress_profit_factor, 4),
            "param_degradation_pct": round(self.param_degradation_pct, 4),
            "passed": self.passed,
            "rejection_reasons": self.rejection_reasons,
        }


class MonteCarloSimulator:
    """Deterministic Monte Carlo bootstrap and friction stress engine."""

    def __init__(self, config: Optional[ValidationThresholdsConfig] = None):
        self.config = config or DEFAULT_VALIDATION_CONFIG

    def run_bootstrap_reshuffle(
        self,
        trades: List[TradeRecord],
        iterations: int = 2000,
        seed: int = 42,
        initial_capital: float = 10000.0,
        ruin_drawdown_pct: float = 0.20,
    ) -> Dict[str, Any]:
        """Bootstrap trade reshuffling with replacement to compute drawdown distribution."""
        if not trades:
            return {
                "median_drawdown_pct": 0.0,
                "p95_drawdown_pct": 0.0,
                "worst_case_drawdown_pct": 0.0,
                "probability_of_ruin": 1.0,
                "p95_losing_streak": 0,
            }

        rng = np.random.default_rng(seed)
        pnl_array = np.array([t.net_pnl for t in trades], dtype=np.float64)
        n_trades = len(pnl_array)

        # Generate (iterations x n_trades) samples with replacement
        sample_indices = rng.integers(0, n_trades, size=(iterations, n_trades))
        sampled_pnls = pnl_array[sample_indices]

        # Cumulative equity curves
        cum_pnl = np.cumsum(sampled_pnls, axis=1)
        equity_paths = initial_capital + cum_pnl

        # Calculate max drawdown for each synthetic path
        peak_equity = np.maximum.accumulate(equity_paths, axis=1)
        peak_equity = np.maximum(peak_equity, initial_capital)
        drawdowns = (peak_equity - equity_paths) / peak_equity

        path_max_drawdowns = np.max(drawdowns, axis=1)

        median_dd = float(np.median(path_max_drawdowns))
        p95_dd = float(np.percentile(path_max_drawdowns, 95))
        worst_dd = float(np.max(path_max_drawdowns))

        # Probability of ruin: fraction of paths hitting >= 20% drawdown
        ruin_count = np.sum(path_max_drawdowns >= ruin_drawdown_pct)
        prob_ruin = float(ruin_count / iterations)

        # Losing streak distribution
        streaks = []
        for row in (sampled_pnls < 0):
            current = 0
            max_s = 0
            for is_loss in row:
                if is_loss:
                    current += 1
                    if current > max_s:
                        max_s = current
                else:
                    current = 0
            streaks.append(max_s)

        p95_streak = int(np.percentile(streaks, 95))

        return {
            "median_drawdown_pct": median_dd,
            "p95_drawdown_pct": p95_dd,
            "worst_case_drawdown_pct": worst_dd,
            "probability_of_ruin": prob_ruin,
            "p95_losing_streak": p95_streak,
        }

    def run_friction_stress(
        self,
        trades: List[TradeRecord],
        friction_factor: float = 1.50,
    ) -> float:
        """Re-evaluate trade PnL under +50% execution friction (spread and slippage)."""
        if not trades:
            return 0.0

        stressed_gross_profits = 0.0
        stressed_gross_losses = 0.0

        for t in trades:
            # Baseline net PnL + fees + slippage is gross PnL
            # Under stress, extra friction = 0.50 * (fees + slippage)
            added_friction = (t.fees + t.slippage) * (friction_factor - 1.0)
            stressed_net_pnl = t.net_pnl - added_friction

            if stressed_net_pnl > 0:
                stressed_gross_profits += stressed_net_pnl
            else:
                stressed_gross_losses += abs(stressed_net_pnl)

        if stressed_gross_losses == 0.0:
            return 10.0 if stressed_gross_profits > 0 else 0.0

        return round(float(stressed_gross_profits / stressed_gross_losses), 4)

    def evaluate(
        self,
        trades: List[TradeRecord],
        iterations: int = 2000,
        param_degradation_pct: float = 0.15,
    ) -> MonteCarloResultData:
        """Run complete Monte Carlo stress testing evaluation."""
        if len(trades) < self.config.MIN_TRADES_GATE:
            return MonteCarloResultData(
                iterations=iterations,
                median_drawdown_pct=0.0,
                p95_drawdown_pct=0.0,
                worst_case_drawdown_pct=0.0,
                probability_of_ruin=1.0,
                p95_losing_streak=0,
                stress_profit_factor=0.0,
                param_degradation_pct=param_degradation_pct,
                passed=False,
                rejection_reasons=[
                    f"Insufficient trades ({len(trades)} < minimum {self.config.MIN_TRADES_GATE}) for Monte Carlo bootstrap."
                ],
            )

        bootstrap = self.run_bootstrap_reshuffle(trades=trades, iterations=iterations)
        stress_gpf = self.run_friction_stress(trades=trades, friction_factor=1.50)

        passed = True
        rejections: List[str] = []

        if bootstrap["probability_of_ruin"] >= self.config.MAX_RUIN_PROBABILITY:
            passed = False
            rejections.append(
                f"Probability of ruin {bootstrap['probability_of_ruin']:.4f} reaches or exceeds maximum threshold {self.config.MAX_RUIN_PROBABILITY:.4f}."
            )

        if stress_gpf < self.config.MIN_STRESS_PROFIT_FACTOR:
            passed = False
            rejections.append(
                f"Friction-stressed Profit Factor {stress_gpf:.4f} below minimum {self.config.MIN_STRESS_PROFIT_FACTOR:.4f}."
            )

        if param_degradation_pct > self.config.MAX_PARAM_DEGRADATION:
            passed = False
            rejections.append(
                f"Parameter plateau degradation {param_degradation_pct:.2f} exceeds allowable maximum {self.config.MAX_PARAM_DEGRADATION:.2f}."
            )

        return MonteCarloResultData(
            iterations=iterations,
            median_drawdown_pct=bootstrap["median_drawdown_pct"],
            p95_drawdown_pct=bootstrap["p95_drawdown_pct"],
            worst_case_drawdown_pct=bootstrap["worst_case_drawdown_pct"],
            probability_of_ruin=bootstrap["probability_of_ruin"],
            p95_losing_streak=bootstrap["p95_losing_streak"],
            stress_profit_factor=stress_gpf,
            param_degradation_pct=param_degradation_pct,
            passed=passed,
            rejection_reasons=rejections,
        )

