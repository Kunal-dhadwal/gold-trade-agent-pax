"""Deterministic cascading early-exit validation pipeline."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import pandas as pd

from src.backtest.engine import BacktestEngine
from src.backtest.types import BacktestConfig, BacktestResult
from src.core.logger import logger
from src.quant.scoring import CompositeStrategyScorer, StrategyScoreResult
from src.quant.validation.config import DEFAULT_VALIDATION_CONFIG, ValidationThresholdsConfig
from src.quant.validation.monte_carlo import MonteCarloResultData, MonteCarloSimulator
from src.quant.validation.walk_forward import WalkForwardAnalysisResult, WalkForwardAnalyzer
from src.strategy.contracts import StrategyProposal
from src.strategy.interpreter import ASTStrategy


@dataclass
class ValidationStageResult:
    stage_name: str
    passed: bool
    details: Dict[str, Any] = field(default_factory=dict)
    rejection_reason: Optional[str] = None


@dataclass
class ValidationPipelineReport:
    """Full cascading validation pipeline report."""
    strategy_id: str
    strategy_name: str
    is_backtest_result: Optional[BacktestResult] = None
    wfa_result: Optional[WalkForwardAnalysisResult] = None
    monte_carlo_result: Optional[MonteCarloResultData] = None
    score_result: Optional[StrategyScoreResult] = None
    stages: List[ValidationStageResult] = field(default_factory=list)
    overall_passed: bool = False
    final_tier: str = "REJECTED"
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "overall_passed": self.overall_passed,
            "final_tier": self.final_tier,
            "stages": [
                {
                    "stage": s.stage_name,
                    "passed": s.passed,
                    "reason": s.rejection_reason,
                    "details": s.details,
                }
                for s in self.stages
            ],
            "score": self.score_result.to_dict() if self.score_result else None,
            "wfa": self.wfa_result.to_dict() if self.wfa_result else None,
            "monte_carlo": self.monte_carlo_result.to_dict() if self.monte_carlo_result else None,
            "completed_at": self.completed_at.isoformat(),
        }


class CascadingValidationPipeline:
    """Orchestrates cascading short-circuiting quantitative validation."""

    def __init__(
        self,
        config: Optional[ValidationThresholdsConfig] = None,
        backtest_config: Optional[BacktestConfig] = None,
    ):
        self.config = config or DEFAULT_VALIDATION_CONFIG
        self.backtest_config = backtest_config or BacktestConfig()
        self.engine = BacktestEngine(self.backtest_config)
        self.wfa_analyzer = WalkForwardAnalyzer(self.config, self.backtest_config)
        self.mc_simulator = MonteCarloSimulator(self.config)
        self.scorer = CompositeStrategyScorer(self.config)

    def run_pipeline(
        self,
        proposal: StrategyProposal,
        df: pd.DataFrame,
        is_df: Optional[pd.DataFrame] = None,
    ) -> ValidationPipelineReport:
        """Run the cascading validation pipeline with strict early-exit short-circuiting."""
        report = ValidationPipelineReport(
            strategy_id=str(proposal.strategy_id),
            strategy_name=proposal.name,
        )

        # STAGE 1: AST Schema Validation (Already passed if proposal instantiated, but compile strategy)
        try:
            strategy = ASTStrategy(proposal=proposal)
            report.stages.append(
                ValidationStageResult(stage_name="AST_VALIDATION", passed=True)
            )
        except Exception as err:
            report.stages.append(
                ValidationStageResult(
                    stage_name="AST_VALIDATION",
                    passed=False,
                    rejection_reason=f"Failed to compile AST strategy: {err}",
                )
            )
            report.final_tier = "REJECTED"
            return report

        # STAGE 2 & 3: In-Sample Backtest & Tier-1 Hard Gates
        eval_is_df = is_df if is_df is not None else df
        inst = proposal.instrument.value
        tf = proposal.timeframe.value
        try:
            is_result = self.engine.run(eval_is_df, strategy, instrument=inst, timeframe=tf)
            report.is_backtest_result = is_result
        except Exception as err:
            report.stages.append(
                ValidationStageResult(
                    stage_name="IN_SAMPLE_BACKTEST",
                    passed=False,
                    rejection_reason=f"In-Sample backtest failed: {err}",
                )
            )
            report.final_tier = "REJECTED"
            return report

        metrics = is_result.metrics
        spread_threshold = self.config.MIN_EXPECTANCY_SPREAD_RATIO * self.backtest_config.spread_usd

        is_gate_passed = True
        is_rejection_reasons = []

        if metrics.total_trades < self.config.MIN_TRADES_GATE:
            is_gate_passed = False
            is_rejection_reasons.append(
                f"Trade count {metrics.total_trades} < minimum gate {self.config.MIN_TRADES_GATE}."
            )

        if metrics.profit_factor < self.config.MIN_GROSS_PROFIT_FACTOR:
            is_gate_passed = False
            is_rejection_reasons.append(
                f"Gross Profit Factor {metrics.profit_factor:.2f} < minimum {self.config.MIN_GROSS_PROFIT_FACTOR:.2f}."
            )

        if metrics.max_drawdown_pct > (self.config.MAX_DRAWDOWN_GATE * 100.0):
            is_gate_passed = False
            is_rejection_reasons.append(
                f"Max Drawdown {metrics.max_drawdown_pct:.2f}% > limit {self.config.MAX_DRAWDOWN_GATE * 100.0:.1f}%."
            )

        if metrics.expectancy_usd < spread_threshold:
            is_gate_passed = False
            is_rejection_reasons.append(
                f"Expectancy ${metrics.expectancy_usd:.2f} < spread threshold ${spread_threshold:.2f}."
            )

        if metrics.max_consecutive_losses > self.config.MAX_CONSECUTIVE_LOSSES:
            is_gate_passed = False
            is_rejection_reasons.append(
                f"Consecutive losses {metrics.max_consecutive_losses} > maximum {self.config.MAX_CONSECUTIVE_LOSSES}."
            )

        stage_details = {
            "trades": metrics.total_trades,
            "profit_factor": metrics.profit_factor,
            "max_drawdown_pct": metrics.max_drawdown_pct,
            "expectancy_usd": metrics.expectancy_usd,
            "consecutive_losses": metrics.max_consecutive_losses,
        }

        if not is_gate_passed:
            # SHORT-CIRCUIT: Do NOT run WFA or Monte Carlo!
            report.stages.append(
                ValidationStageResult(
                    stage_name="IN_SAMPLE_HARD_GATES",
                    passed=False,
                    details=stage_details,
                    rejection_reason="; ".join(is_rejection_reasons),
                )
            )
            report.final_tier = "REJECTED"
            logger.info(
                f"Strategy {proposal.name} REJECTED at In-Sample Hard Gates. Early exit enforced."
            )
            return report

        report.stages.append(
            ValidationStageResult(
                stage_name="IN_SAMPLE_HARD_GATES",
                passed=True,
                details=stage_details,
            )
        )

        # STAGE 4: Walk-Forward Analysis (WFA)
        wfa_res = self.wfa_analyzer.evaluate_strategy(
            df=df,
            strategy=strategy,
            instrument=inst,
            timeframe=tf,
        )
        report.wfa_result = wfa_res

        if not wfa_res.passed:
            # SHORT-CIRCUIT: Do NOT run Monte Carlo!
            report.stages.append(
                ValidationStageResult(
                    stage_name="WALK_FORWARD_ANALYSIS",
                    passed=False,
                    details=wfa_res.to_dict(),
                    rejection_reason="; ".join(wfa_res.rejection_reasons),
                )
            )
            report.final_tier = "REJECTED"
            logger.info(
                f"Strategy {proposal.name} REJECTED at Walk-Forward Analysis. Early exit enforced."
            )
            return report

        report.stages.append(
            ValidationStageResult(
                stage_name="WALK_FORWARD_ANALYSIS",
                passed=True,
                details=wfa_res.to_dict(),
            )
        )

        # STAGE 5: Monte Carlo Stress Testing
        mc_res = self.mc_simulator.evaluate(
            trades=is_result.trades,
            iterations=2000,
            param_degradation_pct=0.15,
        )
        report.monte_carlo_result = mc_res

        if not mc_res.passed:
            report.stages.append(
                ValidationStageResult(
                    stage_name="MONTE_CARLO_STRESS_TEST",
                    passed=False,
                    details=mc_res.to_dict(),
                    rejection_reason="; ".join(mc_res.rejection_reasons),
                )
            )
            report.final_tier = "REJECTED"
            return report

        report.stages.append(
            ValidationStageResult(
                stage_name="MONTE_CARLO_STRESS_TEST",
                passed=True,
                details=mc_res.to_dict(),
            )
        )

        # STAGE 6: Composite Strategy Score (CSS)
        score_res = self.scorer.compute_score(
            sortino_oos=metrics.sortino_ratio,
            max_dd_oos=metrics.max_drawdown_pct / 100.0,
            gpf_oos=metrics.profit_factor,
            wfe=wfa_res.mean_wfe,
            p95_dd_mc=mc_res.p95_drawdown_pct,
            degradation=mc_res.param_degradation_pct,
            passed_hard_gates=True,
        )
        report.score_result = score_res
        report.overall_passed = score_res.tier in ("TIER_1", "TIER_2")
        report.final_tier = score_res.tier

        report.stages.append(
            ValidationStageResult(
                stage_name="COMPOSITE_STRATEGY_SCORE",
                passed=report.overall_passed,
                details=score_res.to_dict(),
                rejection_reason=None if report.overall_passed else score_res.summary,
            )
        )

        return report

