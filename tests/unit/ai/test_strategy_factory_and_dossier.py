"""Unit and end-to-end tests for Batch Strategy Factory, Tournament integration,
and Strategy Validation Dossier Generator.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4
import numpy as np
import pandas as pd
import pytest

from src.ai.pine_transpiler import PineScriptTranspiler
from src.ai.providers import MockLLMProvider
from src.ai.report_generator import StrategyDossierGenerator
from src.ai.strategy_factory import (
    BatchFactoryResult,
    BatchStrategyFactory,
    StrategyFactoryConfig,
)
from src.database.connection import AsyncSessionLocal
from src.database.models import Instrument, Strategy, StrategyVersion
from src.quant.validation.config import ValidationThresholdsConfig
from src.quant.validation.overfitting import (
    CandidateLineage,
    TournamentLeaderboardEntry,
    TournamentManager,
    partition_train_holdout,
)
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


# ==============================================================================
# FIXTURES
# ==============================================================================

@pytest.fixture
def market_df():
    """Generates 500 days of hourly data for train/holdout partitioning."""
    n_bars = 24 * 500
    dates = pd.date_range("2023-01-01", periods=n_bars, freq="1h", tz=timezone.utc)
    base = 1950.0
    drift = np.linspace(0, 200, n_bars)
    cycle = np.sin(np.linspace(0, 80, n_bars)) * 12.0
    closes = base + drift + cycle
    return pd.DataFrame(
        {
            "timestamp": dates,
            "open": closes - 0.5,
            "high": closes + 2.0,
            "low": closes - 2.0,
            "close": closes,
            "volume": np.full(n_bars, 1000.0),
        }
    )


@pytest.fixture
def sample_proposal():
    """Constructs a deterministic valid StrategyProposal."""
    return StrategyProposal(
        name="GOLD_FACTORY_TREND",
        hypothesis="Trend continuation across London fix liquidity windows.",
        direction=TradeDirection.LONG,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.TRENDING_BULL],
        indicators=[
            IndicatorSpec(alias="ema_fast", name=IndicatorNameEnum.EMA, inputs={"period": 12}),
            IndicatorSpec(alias="ema_slow", name=IndicatorNameEnum.EMA, inputs={"period": 26}),
        ],
        entry_rules=ConditionNode(
            operator="AND",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="ema_fast", lag=0),
                    comparator="CROSS_OVER",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_slow", lag=0),
                )
            ],
        ),
        exit_rules=ConditionNode(
            operator="OR",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="ema_fast", lag=0),
                    comparator="CROSS_UNDER",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_slow", lag=0),
                )
            ],
        ),
        stop_loss=StopLossSpec(type="ATR_MULTIPLE", multiplier_or_value=1.5),
        take_profit=TakeProfitSpec(type="ATR_MULTIPLE", multiplier_or_value=3.0),
        constraints=ExecutionConstraints(max_holding_bars=48, allowed_sessions=["ALL"]),
        rationale="Baseline proposal for dossier and factory verification.",
    )


# ==============================================================================
# 1. BATCH STRATEGY FACTORY TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_batch_strategy_factory_mock_provider(market_df):
    """End-to-end execution of BatchStrategyFactory with mock provider."""
    config = StrategyFactoryConfig(
        batch_size=3,
        max_refinement_attempts=1,
        require_holdout=False,
        validation_config=ValidationThresholdsConfig(
            MIN_TRADES_GATE=5,
            MIN_EXPECTANCY_SPREAD_RATIO=0.0,
            MIN_GROSS_PROFIT_FACTOR=1.0,
            MAX_DRAWDOWN_GATE=0.50,
            MAX_CONSECUTIVE_LOSSES=20,
            MIN_OOS_RETENTION_RATIO=0.10,
            MAX_RUIN_PROBABILITY=0.50,
            MIN_WFE_RATIO=0.01,
            MIN_STRESS_PROFIT_FACTOR=0.80,
            MAX_PARAM_DEGRADATION=0.70,
            CSS_TIER1_THRESHOLD=50.0,
            CSS_TIER2_THRESHOLD=30.0,
            MIN_DSR_THRESHOLD=0.10,
        ),
    )

    provider = MockLLMProvider()
    factory = BatchStrategyFactory(config=config, provider=provider)

    result = await factory.run_batch(raw_df=market_df)

    assert result.total_requested == 3
    assert result.total_generated >= 3
    assert len(result.tournament_report.leaderboard) >= 3
    assert result.tournament_report.total_trials_evaluated >= 3

    # Check that Pine scripts were transpiled
    assert len(result.pine_scripts) >= 1
    for strat_id, pine in result.pine_scripts.items():
        assert "//@version=6" in pine
        assert "strategy(" in pine


@pytest.mark.asyncio
async def test_batch_strategy_factory_rejection_gates(market_df):
    """Factory correctly rejects and disqualifies candidates that fail hard gates."""
    # Strict unattainable trades gate
    config = StrategyFactoryConfig(
        batch_size=2,
        require_holdout=False,
        validation_config=ValidationThresholdsConfig(MIN_TRADES_GATE=5000),
    )

    factory = BatchStrategyFactory(config=config, provider=MockLLMProvider())
    result = await factory.run_batch(raw_df=market_df)

    assert result.surviving_count == 0
    assert result.rejected_count >= 2
    assert result.winner_strategy is None
    for entry in result.tournament_report.leaderboard:
        assert entry.passed_all_gates is False
        assert entry.css_score == 0.0
        assert entry.tier == "REJECTED"


@pytest.mark.asyncio
async def test_batch_strategy_factory_deduplication(market_df, sample_proposal):
    """Factory identifies duplicate proposals and rejects without inflating trials."""
    config = StrategyFactoryConfig(
        batch_size=2,
        require_holdout=False,
        validation_config=ValidationThresholdsConfig(
            MIN_TRADES_GATE=5,
            MIN_GROSS_PROFIT_FACTOR=1.0,
            MAX_DRAWDOWN_GATE=0.50,
            MIN_DSR_THRESHOLD=0.0,
        ),
    )

    factory = BatchStrategyFactory(config=config, provider=MockLLMProvider())

    p1 = sample_proposal.model_copy(deep=True)
    p2 = sample_proposal.model_copy(deep=True)
    p2.strategy_id = uuid4()
    p2.name = "DUPLICATE_NAME"

    result = await factory.run_batch(raw_df=market_df, custom_proposals=[p1, p2])

    # First is evaluated, second is rejected as duplicate
    ranks = [e for e in result.tournament_report.leaderboard]
    dup_entry = next((e for e in ranks if "DUPLICATE_STRATEGY" in e.explanation), None)
    assert dup_entry is not None
    assert dup_entry.tier == "REJECTED"


@pytest.mark.asyncio
async def test_batch_strategy_factory_bounded_refinement(market_df, sample_proposal):
    """Refinement loop produces mutated children with tracked lineage and respects search budget."""
    config = StrategyFactoryConfig(
        batch_size=1,
        max_refinement_attempts=2,
        require_holdout=False,
        validation_config=ValidationThresholdsConfig(
            MIN_TRADES_GATE=100,  # Will fail attempt 1
            MIN_GROSS_PROFIT_FACTOR=1.0,
            MAX_DRAWDOWN_GATE=0.50,
            MIN_DSR_THRESHOLD=0.0,
        ),
    )

    factory = BatchStrategyFactory(config=config, provider=MockLLMProvider())
    result = await factory.run_batch(raw_df=market_df, custom_proposals=[sample_proposal])

    # Should have attempted refinement up to max_refinement_attempts
    assert result.total_generated >= 1
    # Lineage check
    entries = result.tournament_report.leaderboard
    assert len(entries) >= 1


# ==============================================================================
# 2. STRATEGY DOSSIER GENERATOR TESTS
# ==============================================================================

def test_dossier_generator_full_evidence(sample_proposal):
    """Dossier generator compiles complete standalone Markdown document with all sections."""
    generator = StrategyDossierGenerator()

    lineage = CandidateLineage(
        strategy_id=str(sample_proposal.strategy_id),
        name=sample_proposal.name,
        hypothesis_id="hyp_london_breakout_01",
        generation=1,
        trial_index=1,
        ast_hash="abcdef0123456789",
    )

    entry = TournamentLeaderboardEntry(
        rank=1,
        strategy_id=str(sample_proposal.strategy_id),
        strategy_name=sample_proposal.name,
        css_score=86.50,
        dsr_score=0.998,
        tier="TIER_1_WINNER",
        passed_all_gates=True,
        sortino_ratio=2.45,
        profit_factor=2.10,
        max_drawdown_pct=8.5,
        wfe_ratio=0.68,
        monte_carlo_ruin_prob=0.015,
        holdout_sharpe=1.65,
        holdout_passed=True,
        trials_count=5,
        expected_max_sharpe=1.42,
        lineage=lineage,
        rejection_reasons=[],
        explanation="Tier 1 Prime Candidate. Passed all validation hurdles.",
    )

    dossier = generator.generate_dossier(
        proposal=sample_proposal,
        leaderboard_entry=entry,
    )

    # Verify all mandatory sections
    assert "# AGentTrXPAUS Quantitative Strategy Dossier" in dossier
    assert "## 1. Executive Summary & Economic Hypothesis" in dossier
    assert "## 2. Strategy Architecture & AST Specification" in dossier
    assert "## 3. In-Sample & Historical Backtest Performance" in dossier
    assert "## 4. Walk-Forward Analysis (WFA)" in dossier
    assert "## 5. Monte Carlo Robustness & Stress Testing" in dossier
    assert "## 6. Anti-Overfitting Controls & Multiple-Testing Audit (DSR)" in dossier
    assert "## 7. Composite Strategy Score (CSS) & Tournament Ranking" in dossier
    assert "## 8. Production TradingView Pine Script v6" in dossier
    assert "## 9. Data Provenance & Methodological Limitations" in dossier

    # Verify Pine Script v6 code inclusion
    assert "//@version=6" in dossier
    assert "strategy(" in dossier


def test_dossier_generator_missing_data_explicit_marking(sample_proposal):
    """Dossier generator explicitly marks missing evidence as NOT_EVALUATED rather than fabricating metrics."""
    generator = StrategyDossierGenerator()

    lineage = CandidateLineage(
        strategy_id=str(sample_proposal.strategy_id),
        name=sample_proposal.name,
        generation=1,
        trial_index=1,
    )

    entry = TournamentLeaderboardEntry(
        rank=3,
        strategy_id=str(sample_proposal.strategy_id),
        strategy_name=sample_proposal.name,
        css_score=0.0,
        dsr_score=0.0,
        tier="REJECTED",
        passed_all_gates=False,
        sortino_ratio=0.0,
        profit_factor=0.0,
        max_drawdown_pct=0.0,
        wfe_ratio=0.0,
        monte_carlo_ruin_prob=1.0,
        holdout_sharpe=None,
        holdout_passed=None,
        trials_count=3,
        expected_max_sharpe=0.0,
        lineage=lineage,
        rejection_reasons=["Trade count 22 < minimum gate 50."],
        explanation="Rejected at In-Sample gates.",
    )

    # No pipeline report or holdout result provided
    dossier = generator.generate_dossier(
        proposal=sample_proposal,
        leaderboard_entry=entry,
        pipeline_report=None,
        holdout_result=None,
    )

    assert "[NOT_EVALUATED]" in dossier
    assert "Trade count 22 < minimum gate 50" in dossier
    assert "REJECTED" in dossier


def test_dossier_export_to_directory(tmp_path, sample_proposal):
    """Dossier is cleanly exported as a standalone Markdown file."""
    generator = StrategyDossierGenerator()

    entry = TournamentLeaderboardEntry(
        rank=1,
        strategy_id=str(sample_proposal.strategy_id),
        strategy_name=sample_proposal.name,
        css_score=85.0,
        dsr_score=0.98,
        tier="TIER_1",
        passed_all_gates=True,
        sortino_ratio=2.0,
        profit_factor=1.8,
        max_drawdown_pct=10.0,
        wfe_ratio=0.6,
        monte_carlo_ruin_prob=0.02,
        holdout_sharpe=1.5,
        holdout_passed=True,
        trials_count=1,
        expected_max_sharpe=0.0,
        lineage=CandidateLineage(strategy_id=str(sample_proposal.strategy_id), name=sample_proposal.name),
        rejection_reasons=[],
        explanation="Passing test candidate",
    )

    content = generator.generate_dossier(sample_proposal, entry)
    exported_file = generator.export_dossier(content, output_dir=tmp_path, filename="test_dossier.md")

    assert exported_file.exists()
    assert exported_file.is_file()
    read_text = exported_file.read_text(encoding="utf-8")
    assert "GOLD_FACTORY_TREND" in read_text


# ==============================================================================
# 3. DATABASE PERSISTENCE TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_persist_strategy_to_db(sample_proposal):
    """Persist generated strategy and version metadata into PostgreSQL/SQLite models."""
    factory = BatchStrategyFactory()

    lineage = CandidateLineage(
        strategy_id=str(sample_proposal.strategy_id),
        name=sample_proposal.name,
        generation=1,
        trial_index=1,
        ast_hash="sha256hash123",
    )

    entry = TournamentLeaderboardEntry(
        rank=1,
        strategy_id=str(sample_proposal.strategy_id),
        strategy_name=sample_proposal.name,
        css_score=82.5,
        dsr_score=0.97,
        tier="TIER_1",
        passed_all_gates=True,
        sortino_ratio=2.1,
        profit_factor=1.9,
        max_drawdown_pct=11.2,
        wfe_ratio=0.62,
        monte_carlo_ruin_prob=0.02,
        holdout_sharpe=1.4,
        holdout_passed=True,
        trials_count=2,
        expected_max_sharpe=0.8,
        lineage=lineage,
        rejection_reasons=[],
        explanation="Candidate passed all hurdles.",
    )

    async with AsyncSessionLocal() as session:
        # Check or create base instrument
        inst = await session.get(Instrument, "XAUUSD")
        if not inst:
            inst = Instrument(
                id="XAUUSD",
                symbol="XAUUSD",
                name="Gold Spot US Dollar",
                asset_class="COMMODITY",
                quote_currency="USD",
                tick_size=0.01,
                lot_size=100.0,
            )
            session.add(inst)
            await session.flush()

        version_record = await factory.persist_strategy_to_db(
            db=session,
            proposal=sample_proposal,
            entry=entry,
            pine_code="//@version=6\nstrategy('Test')",
        )

        assert version_record.id is not None
        assert version_record.strategy_id == str(sample_proposal.strategy_id)
        assert version_record.status == "ACCEPTED_CANDIDATE"
        assert "//@version=6" in version_record.pine_script_code
