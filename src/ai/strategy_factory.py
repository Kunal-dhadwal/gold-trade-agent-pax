"""Batch Strategy Factory for multi-candidate XAUUSD strategy generation and validation.

Governing specifications:
- ADR-004: AI-to-Deterministic Boundary Enforcement
- ADR-008: Anti-Overfitting Architecture, OOS Quarantine & Configurable Validation Gates
- ADR-010: Composite Strategy Scoring & Quantitative Tiering
- Milestone 2.5.3: Batch Strategy Factory & Validation Dossier Generator
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from uuid import UUID, uuid4
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents import (
    MarketAnalystAgent,
    RegimeAnalystAgent,
    StrategyResearcherAgent,
)
from src.ai.pine_transpiler import PineScriptTranspiler, PineStaticValidator
from src.ai.providers import MockLLMProvider
from src.ai.providers.base import LLMProvider
from src.ai.summarizer import (
    ISDiagnosticRecord,
    MarketBrief,
    MarketFeatureSummarizer,
    StrategyResearchContext,
)
from src.backtest.types import BacktestConfig
from src.core.logger import logger
from src.database.models import (
    Backtest,
    Instrument,
    ResearchHypothesis,
    ResearchSession,
    Strategy,
    StrategyParameter,
    StrategyVersion,
    ValidationResult,
)
from src.quant.validation.config import DEFAULT_VALIDATION_CONFIG, ValidationThresholdsConfig
from src.quant.validation.overfitting import (
    CandidateLineage,
    DSRResult,
    HoldoutValidationGate,
    HoldoutValidationResult,
    TournamentLeaderboardEntry,
    TournamentManager,
    TournamentReport,
    calculate_dsr,
    compute_strategy_hash,
    partition_train_holdout,
)
from src.quant.validation.pipeline import CascadingValidationPipeline, ValidationPipelineReport
from src.strategy.contracts import (
    ConditionComparison,
    ConditionNode,
    DataSourceEnum,
    ExecutionConstraints,
    IndicatorNameEnum,
    IndicatorSpec,
    InstrumentEnum,
    RegimeEnum,
    StopLossSpec,
    StrategyProposal,
    TakeProfitSpec,
    TimeframeEnum,
    TradeDirection,
    ValueRef,
    ValueRefType,
)


@dataclass
class StrategyFactoryConfig:
    """Configuration for the Batch Strategy Factory."""
    instrument: InstrumentEnum = InstrumentEnum.XAUUSD
    timeframes: List[TimeframeEnum] = field(default_factory=lambda: [TimeframeEnum.H1])
    regimes: List[RegimeEnum] = field(
        default_factory=lambda: [
            RegimeEnum.TRENDING_BULL,
            RegimeEnum.TRENDING_BEAR,
            RegimeEnum.RANGING,
            RegimeEnum.BREAKOUT,
        ]
    )
    batch_size: int = 5
    max_refinement_attempts: int = 2
    max_search_budget_per_hypothesis: int = 10  # ADR-008 quota
    holdout_ratio: float = 0.20
    require_holdout: bool = True
    min_dsr_threshold: float = 0.95
    validation_config: Optional[ValidationThresholdsConfig] = None
    backtest_config: Optional[BacktestConfig] = None


@dataclass
class BatchFactoryResult:
    """Result of a batch strategy factory run."""
    batch_id: str
    total_requested: int
    total_generated: int
    surviving_count: int
    rejected_count: int
    tournament_report: TournamentReport
    proposals: Dict[str, StrategyProposal] = field(default_factory=dict)
    pipeline_reports: Dict[str, ValidationPipelineReport] = field(default_factory=dict)
    pine_scripts: Dict[str, str] = field(default_factory=dict)
    holdout_results: Dict[str, HoldoutValidationResult] = field(default_factory=dict)
    winner_strategy: Optional[StrategyProposal] = None
    winner_entry: Optional[TournamentLeaderboardEntry] = None
    winner_pine_code: Optional[str] = None
    generation_failures: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "total_requested": self.total_requested,
            "total_generated": self.total_generated,
            "surviving_count": self.surviving_count,
            "rejected_count": self.rejected_count,
            "winner_strategy_id": str(self.winner_strategy.strategy_id) if self.winner_strategy else None,
            "winner_rank": self.winner_entry.rank if self.winner_entry else None,
            "winner_css": self.winner_entry.css_score if self.winner_entry else None,
            "winner_tier": self.winner_entry.tier if self.winner_entry else None,
            "generation_failures": self.generation_failures,
            "tournament_report": self.tournament_report.to_dict(),
            "created_at": self.created_at.isoformat(),
        }


class BatchStrategyFactory:
    """Generates, refines, validates, and ranks diverse batches of XAUUSD trading strategies.

    ANTI-OVERFITTING INVARIANT:
    The final 20% holdout partition is strictly air-gapped and NEVER passed to agents,
    feature summarizers, or refinement loops. It is evaluated exclusively by
    HoldoutValidationGate after a candidate passes preceding In-Sample stages.
    """

    def __init__(
        self,
        config: Optional[StrategyFactoryConfig] = None,
        provider: Optional[LLMProvider] = None,
        tournament_manager: Optional[TournamentManager] = None,
    ):
        self.config = config or StrategyFactoryConfig()
        self.provider = provider or MockLLMProvider()
        self.validation_config = self.config.validation_config or DEFAULT_VALIDATION_CONFIG
        self.backtest_config = self.config.backtest_config or BacktestConfig()

        # Agents
        self.market_analyst = MarketAnalystAgent(provider=self.provider)
        self.regime_analyst = RegimeAnalystAgent(provider=self.provider)
        self.strategy_researcher = StrategyResearcherAgent(provider=self.provider)

        # Quantitative Pipeline & Components
        self.summarizer = MarketFeatureSummarizer()
        self.pipeline = CascadingValidationPipeline(self.validation_config, self.backtest_config)
        self.tournament_manager = tournament_manager or TournamentManager(
            config=self.validation_config,
            backtest_config=self.backtest_config,
            min_dsr_threshold=self.config.min_dsr_threshold,
            require_holdout=self.config.require_holdout,
        )
        self.transpiler = PineScriptTranspiler()
        self.validator = PineStaticValidator()
        self.holdout_gate = HoldoutValidationGate(self.validation_config, self.backtest_config)

    def _create_archetype_proposal(
        self,
        name: str,
        regime: RegimeEnum,
        timeframe: TimeframeEnum,
        direction: TradeDirection,
        archetype: str,
        hypothesis: str,
        fast_period: int = 12,
        slow_period: int = 26,
    ) -> StrategyProposal:
        """Create a deterministic baseline archetypal StrategyProposal for diverse exploration."""
        if archetype == "TREND_FOLLOWING":
            indicators = [
                IndicatorSpec(alias="ema_fast", name=IndicatorNameEnum.EMA, inputs={"period": fast_period}),
                IndicatorSpec(alias="ema_slow", name=IndicatorNameEnum.EMA, inputs={"period": slow_period}),
            ]
            entry_cond = ConditionNode(
                operator="AND",
                conditions=[
                    ConditionComparison(
                        left=ValueRef(type=ValueRefType.INDICATOR, name="ema_fast", lag=0),
                        comparator="CROSS_OVER" if direction == TradeDirection.LONG else "CROSS_UNDER",
                        right=ValueRef(type=ValueRefType.INDICATOR, name="ema_slow", lag=0),
                    )
                ],
            )
            exit_cond = ConditionNode(
                operator="OR",
                conditions=[
                    ConditionComparison(
                        left=ValueRef(type=ValueRefType.INDICATOR, name="ema_fast", lag=0),
                        comparator="CROSS_UNDER" if direction == TradeDirection.LONG else "CROSS_OVER",
                        right=ValueRef(type=ValueRefType.INDICATOR, name="ema_slow", lag=0),
                    )
                ],
            )
        elif archetype == "MEAN_REVERSION":
            indicators = [
                IndicatorSpec(alias="rsi_14", name=IndicatorNameEnum.RSI, inputs={"period": 14}),
                IndicatorSpec(alias="ema_filter", name=IndicatorNameEnum.EMA, inputs={"period": 50}),
            ]
            entry_cond = ConditionNode(
                operator="AND",
                conditions=[
                    ConditionComparison(
                        left=ValueRef(type=ValueRefType.INDICATOR, name="rsi_14", lag=0),
                        comparator="<" if direction == TradeDirection.LONG else ">",
                        right=ValueRef(type=ValueRefType.CONSTANT, value=30.0 if direction == TradeDirection.LONG else 70.0),
                    )
                ],
            )
            exit_cond = ConditionNode(
                operator="OR",
                conditions=[
                    ConditionComparison(
                        left=ValueRef(type=ValueRefType.INDICATOR, name="rsi_14", lag=0),
                        comparator=">" if direction == TradeDirection.LONG else "<",
                        right=ValueRef(type=ValueRefType.CONSTANT, value=50.0),
                    )
                ],
            )
        else:  # BREAKOUT
            indicators = [
                IndicatorSpec(alias="donchian_chan", name=IndicatorNameEnum.DONCHIAN, inputs={"period": 20}),
                IndicatorSpec(alias="atr_vol", name=IndicatorNameEnum.ATR, inputs={"period": 14}),
            ]
            entry_cond = ConditionNode(
                operator="AND",
                conditions=[
                    ConditionComparison(
                        left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                        comparator=">" if direction == TradeDirection.LONG else "<",
                        right=ValueRef(type=ValueRefType.INDICATOR, name="donchian_chan", lag=1),
                    )
                ],
            )
            exit_cond = ConditionNode(
                operator="OR",
                conditions=[
                    ConditionComparison(
                        left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                        comparator="<" if direction == TradeDirection.LONG else ">",
                        right=ValueRef(type=ValueRefType.INDICATOR, name="donchian_chan", lag=1),
                    )
                ],
            )

        return StrategyProposal(
            name=name,
            hypothesis=hypothesis,
            direction=direction,
            instrument=self.config.instrument,
            timeframe=timeframe,
            data_source=DataSourceEnum.DUKASCOPY,
            target_regimes=[regime],
            indicators=indicators,
            entry_rules=entry_cond,
            exit_rules=exit_cond,
            stop_loss=StopLossSpec(type="ATR_MULTIPLE", multiplier_or_value=1.5),
            take_profit=TakeProfitSpec(type="ATR_MULTIPLE", multiplier_or_value=3.0),
            constraints=ExecutionConstraints(max_holding_bars=48, allowed_sessions=["ALL"]),
            rationale=f"Systematic {archetype} hypothesis tailored for {regime.value} regime.",
        )

    async def generate_candidate(
        self,
        context: StrategyResearchContext,
        candidate_idx: int,
        archetype_fallback: Optional[str] = None,
    ) -> StrategyProposal:
        """Query StrategyResearcherAgent to formulate proposal AST, or use diverse archetype template."""
        try:
            proposal = await self.strategy_researcher.propose_strategy(context=context)
            return proposal
        except Exception as err:
            logger.warning(
                f"Agent 3 LLM generation failed or returned invalid AST: {err}. "
                f"Falling back to diverse deterministic archetype for candidate {candidate_idx}."
            )
            archetypes = ["TREND_FOLLOWING", "BREAKOUT", "MEAN_REVERSION"]
            arch = archetype_fallback or archetypes[candidate_idx % len(archetypes)]
            timeframe = self.config.timeframes[candidate_idx % len(self.config.timeframes)]
            direction = TradeDirection.LONG if candidate_idx % 2 == 0 else TradeDirection.SHORT

            return self._create_archetype_proposal(
                name=f"XAU_{arch}_{timeframe.value.upper()}_{candidate_idx + 1}",
                regime=context.target_regime,
                timeframe=timeframe,
                direction=direction,
                archetype=arch,
                hypothesis=context.thesis_statement,
                fast_period=10 + (candidate_idx * 2),
                slow_period=24 + (candidate_idx * 4),
            )

    async def run_batch(
        self,
        raw_df: pd.DataFrame,
        batch_id: Optional[str] = None,
        custom_proposals: Optional[List[StrategyProposal]] = None,
    ) -> BatchFactoryResult:
        """Execute end-to-end multi-candidate generation, cascading validation, and tournament ranking."""
        b_id = batch_id or f"batch_{uuid4().hex[:8]}"

        # 1. Enforce strict train (80%) vs quarantined holdout (20%) separation
        train_df, holdout_df = partition_train_holdout(raw_df, holdout_ratio=self.config.holdout_ratio)

        # 2. Extract In-Sample market features
        market_brief = self.summarizer.summarize(
            train_df,
            symbol=self.config.instrument.value,
            timeframe=self.config.timeframes[0].value,
        )

        # 3. Market and Regime analysis (In-Sample only)
        market_report = await self.market_analyst.analyze(brief=market_brief)
        regime_report = await self.regime_analyst.analyze(brief=market_brief)

        proposals: List[StrategyProposal] = []
        proposals_map: Dict[str, StrategyProposal] = {}
        pipeline_reports_map: Dict[str, ValidationPipelineReport] = {}
        pine_scripts_map: Dict[str, str] = {}
        holdout_results_map: Dict[str, HoldoutValidationResult] = {}
        failures: List[str] = []

        # Populate custom proposals or generate batch
        if custom_proposals:
            for p in custom_proposals:
                proposals.append(p)
                proposals_map[str(p.strategy_id)] = p
        else:
            for idx in range(self.config.batch_size):
                regime = self.config.regimes[idx % len(self.config.regimes)]
                tf = self.config.timeframes[idx % len(self.config.timeframes)]
                hypothesis = (
                    f"Gold edge in {regime.value} ({tf.value}): "
                    f"Structural liquidity and volume patterns identified by market structure analysis."
                )

                context = StrategyResearchContext(
                    thesis_statement=hypothesis,
                    target_regime=regime,
                    market_brief=market_brief,
                )

                try:
                    proposal = await self.generate_candidate(context=context, candidate_idx=idx)
                    proposals.append(proposal)
                    proposals_map[str(proposal.strategy_id)] = proposal
                except Exception as gen_err:
                    failures.append(f"Candidate {idx + 1} generation failed: {gen_err}")

        # 4. Cascading Validation and Tournament Evaluation
        for prop in proposals:
            strat_id = str(prop.strategy_id)
            ast_hash = compute_strategy_hash(prop)
            lineage = CandidateLineage(
                strategy_id=strat_id,
                name=prop.name,
                ast_hash=ast_hash,
                trial_index=self.tournament_manager.total_trials_evaluated + 1,
            )

            # Evaluate through TournamentManager (evaluates train_df first; holdout evaluated only if passed)
            entry = self.tournament_manager.evaluate_proposal(
                proposal=prop,
                train_df=train_df,
                holdout_df=holdout_df if self.config.require_holdout else None,
                lineage=lineage,
            )

            # Also capture detailed ValidationPipelineReport for dossier generator
            pipeline_rep = self.pipeline.run_pipeline(prop, train_df)
            pipeline_reports_map[strat_id] = pipeline_rep

            # 5. Bounded Refinement if candidate failed In-Sample gates
            current_prop = prop
            refinements = 0
            while (
                not pipeline_rep.overall_passed
                and refinements < self.config.max_refinement_attempts
                and self.tournament_manager.total_trials_evaluated < self.config.max_search_budget_per_hypothesis
            ):
                refinements += 1
                failure_reason = pipeline_rep.stages[-1].rejection_reason if pipeline_rep.stages else "Unknown gate"
                feedback = f"Attempt {refinements} failed: {failure_reason}. Adjust parameters."

                diag_record = ISDiagnosticRecord(
                    strategy_name=current_prop.name,
                    is_sharpe=pipeline_rep.is_backtest_result.metrics.sharpe_ratio if pipeline_rep.is_backtest_result else 0.0,
                    is_profit_factor=pipeline_rep.is_backtest_result.metrics.profit_factor if pipeline_rep.is_backtest_result else 0.0,
                    is_max_drawdown_pct=pipeline_rep.is_backtest_result.metrics.max_drawdown_pct if pipeline_rep.is_backtest_result else 0.0,
                    is_trades_count=pipeline_rep.is_backtest_result.metrics.total_trades if pipeline_rep.is_backtest_result else 0,
                    passed_is_gates=pipeline_rep.overall_passed,
                    failure_reason=failure_reason,
                )

                refine_ctx = StrategyResearchContext(
                    thesis_statement=current_prop.hypothesis,
                    target_regime=current_prop.target_regimes[0] if current_prop.target_regimes else RegimeEnum.RANGING,
                    market_brief=market_brief,
                    is_diagnostic_history=[diag_record],
                )

                try:
                    refined_prop = await self.strategy_researcher.propose_strategy(context=refine_ctx, feedback=feedback)
                    refined_id = str(refined_prop.strategy_id)
                    ref_hash = compute_strategy_hash(refined_prop)

                    ref_lineage = CandidateLineage(
                        strategy_id=refined_id,
                        name=refined_prop.name,
                        parent_strategy_id=strat_id,
                        generation=refinements + 1,
                        ast_hash=ref_hash,
                        trial_index=self.tournament_manager.total_trials_evaluated + 1,
                    )

                    entry = self.tournament_manager.evaluate_proposal(
                        proposal=refined_prop,
                        train_df=train_df,
                        holdout_df=holdout_df if self.config.require_holdout else None,
                        lineage=ref_lineage,
                    )
                    pipeline_rep = self.pipeline.run_pipeline(refined_prop, train_df)

                    proposals_map[refined_id] = refined_prop
                    pipeline_reports_map[refined_id] = pipeline_rep
                    current_prop = refined_prop
                    strat_id = refined_id
                except Exception as ref_err:
                    failures.append(f"Refinement {refinements} for {prop.name} failed: {ref_err}")
                    break

            # 6. Transpile to Pine Script v6 for eligible or completed strategies
            try:
                pine_code = self.transpiler.transpile(current_prop)
                pine_scripts_map[strat_id] = pine_code
            except Exception as pine_err:
                logger.warning(f"Failed to transpile strategy {strat_id} to Pine Script: {pine_err}")

        # 7. Tournament Report
        tournament_report = self.tournament_manager.get_leaderboard()
        winner_entry = tournament_report.winner
        winner_strategy = proposals_map.get(winner_entry.strategy_id) if winner_entry else None
        winner_pine = pine_scripts_map.get(winner_entry.strategy_id) if winner_entry else None

        return BatchFactoryResult(
            batch_id=b_id,
            total_requested=self.config.batch_size,
            total_generated=len(proposals_map),
            surviving_count=tournament_report.surviving_count,
            rejected_count=tournament_report.rejected_count,
            tournament_report=tournament_report,
            proposals=proposals_map,
            pipeline_reports=pipeline_reports_map,
            pine_scripts=pine_scripts_map,
            holdout_results=holdout_results_map,
            winner_strategy=winner_strategy,
            winner_entry=winner_entry,
            winner_pine_code=winner_pine,
            generation_failures=failures,
        )

    async def persist_strategy_to_db(
        self,
        db: AsyncSession,
        proposal: StrategyProposal,
        entry: TournamentLeaderboardEntry,
        pine_code: Optional[str] = None,
        pipeline_report: Optional[ValidationPipelineReport] = None,
        session_id: Optional[str] = None,
    ) -> StrategyVersion:
        """Persist strategy proposal, version, parameters, backtest, and validation results to database."""
        now = datetime.now(timezone.utc)
        strat_id = str(proposal.strategy_id)
        version_id = str(uuid4())

        # 1. Base Strategy record
        strat_record = await db.get(Strategy, strat_id)
        if not strat_record:
            strat_record = Strategy(
                id=strat_id,
                name=proposal.name,
                category=proposal.target_regimes[0].value if proposal.target_regimes else "QUANT",
                description=proposal.hypothesis,
                author="Agent-3-StrategyResearcher",
                created_at=now,
                updated_at=now,
            )
            db.add(strat_record)
            await db.flush()

        # 2. StrategyVersion record
        version_record = StrategyVersion(
            id=version_id,
            strategy_id=strat_id,
            version_tag=f"v{entry.lineage.generation}",
            timeframe=proposal.timeframe.value,
            status="ACCEPTED_CANDIDATE" if entry.passed_all_gates else "REJECTED",
            logic_description=proposal.rationale,
            entry_rules=proposal.entry_rules.model_dump(),
            exit_rules=proposal.exit_rules.model_dump(),
            stop_loss_logic=proposal.stop_loss.model_dump(),
            take_profit_logic=proposal.take_profit.model_dump(),
            position_sizing_logic={"type": "PERCENT_OF_EQUITY", "value": 100.0},
            pine_script_code=pine_code,
            hypothesis_id=entry.lineage.hypothesis_id,
            created_at=now,
            updated_at=now,
        )
        db.add(version_record)
        await db.flush()

        # 3. StrategyParameters
        for param in proposal.parameters:
            p_rec = StrategyParameter(
                strategy_version_id=version_id,
                name=param.name,
                param_type=param.type.value,
                default_value=str(param.default_value),
                min_value=str(param.min_value) if param.min_value is not None else None,
                max_value=str(param.max_value) if param.max_value is not None else None,
                step=str(param.step) if param.step is not None else None,
            )
            db.add(p_rec)

        # 4. Backtest & ValidationResult if available
        if pipeline_report and pipeline_report.is_backtest_result:
            bt_metrics = pipeline_report.is_backtest_result.metrics
            def _to_dt(val: Any) -> datetime:
                if isinstance(val, datetime):
                    return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
                if isinstance(val, str) and val:
                    try:
                        dt = datetime.fromisoformat(val)
                        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        pass
                return now

            raw_start = getattr(pipeline_report.is_backtest_result, "start_date", None)
            raw_end = getattr(pipeline_report.is_backtest_result, "end_date", None)
            backtest_id = str(uuid4())

            bt_rec = Backtest(
                id=backtest_id,
                strategy_version_id=version_id,
                instrument_id=proposal.instrument.value,
                timeframe=proposal.timeframe.value,
                start_date=_to_dt(raw_start),
                end_date=_to_dt(raw_end),
                initial_capital=bt_metrics.initial_capital,
                final_equity=bt_metrics.final_equity,
                total_net_profit=bt_metrics.total_net_profit,
                total_return_pct=bt_metrics.total_return_pct,
                cagr_pct=bt_metrics.cagr_pct,
                sharpe_ratio=bt_metrics.sharpe_ratio,
                sortino_ratio=bt_metrics.sortino_ratio,
                profit_factor=bt_metrics.profit_factor,
                win_rate_pct=bt_metrics.win_rate_pct,
                max_drawdown_pct=bt_metrics.max_drawdown_pct,
                max_drawdown_usd=bt_metrics.max_drawdown_usd,
                recovery_factor=bt_metrics.recovery_factor,
                expectancy_usd=bt_metrics.expectancy_usd,
                average_r=bt_metrics.average_r,
                total_trades=bt_metrics.total_trades,
                winning_trades=bt_metrics.winning_trades,
                losing_trades=bt_metrics.losing_trades,
                max_consecutive_losses=bt_metrics.max_consecutive_losses,
                total_fees_paid=bt_metrics.total_fees_paid,
                total_slippage_cost=bt_metrics.total_slippage_cost,
                created_at=now,
            )
            db.add(bt_rec)
            await db.flush()

            val_rec = ValidationResult(
                id=str(uuid4()),
                backtest_id=backtest_id,
                oos_start_date=now,
                oos_end_date=now,
                oos_sharpe=entry.holdout_sharpe or bt_metrics.sharpe_ratio or 0.0,
                oos_profit_factor=bt_metrics.profit_factor or 0.0,
                oos_drawdown_pct=bt_metrics.max_drawdown_pct or 0.0,
                degradation_ratio=0.0,
                is_oos_passed=entry.passed_all_gates,
                robustness_score=entry.css_score,
                rejection_reasons={"reasons": entry.rejection_reasons},
                created_at=now,
            )
            db.add(val_rec)

        await db.commit()
        return version_record
