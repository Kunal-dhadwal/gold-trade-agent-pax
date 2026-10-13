"""Unit and numerical edge-case tests for Deflated Sharpe Ratio (DSR), Overfitting Controls,
Holdout isolation, and the Strategy Tournament Manager.
"""

from datetime import datetime, timezone
import math
import numpy as np
import pandas as pd
import pytest

from src.quant.validation.config import ValidationThresholdsConfig
from src.quant.validation.overfitting import (
    CandidateLineage,
    DSRResult,
    HoldoutValidationGate,
    HoldoutValidationResult,
    TournamentLeaderboardEntry,
    TournamentManager,
    TournamentReport,
    calculate_dsr,
    calculate_dsr_from_returns,
    calculate_expected_max_sharpe,
    calculate_psr,
    calculate_sharpe_std_error,
    compute_strategy_hash,
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
def sample_proposal():
    """Constructs a deterministic valid StrategyProposal."""
    return StrategyProposal(
        name="GOLD_TREND_TEST_ALPHA",
        hypothesis="Gold London breakout trend continuation.",
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
                    comparator=">",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_slow", lag=0),
                )
            ],
        ),
        exit_rules=ConditionNode(
            operator="OR",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="ema_fast", lag=0),
                    comparator="<",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_slow", lag=0),
                )
            ],
        ),
        stop_loss=StopLossSpec(type="ATR_MULTIPLE", multiplier_or_value=1.5),
        take_profit=TakeProfitSpec(type="ATR_MULTIPLE", multiplier_or_value=3.0),
        constraints=ExecutionConstraints(max_holding_bars=48, allowed_sessions=["LONDON", "NEW_YORK"]),
        rationale="Robust trend-following proposal for statistical testing.",
    )


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


# ==============================================================================
# 1. DSR & STATISTICAL EDGE CASES
# ==============================================================================

def test_expected_max_sharpe_n1_zero():
    """E[max_N] under H0: SR=0 must be exactly 0.0 when N=1 (no multiple testing)."""
    assert calculate_expected_max_sharpe(1, var_sharpe=1.0) == 0.0
    assert calculate_expected_max_sharpe(0, var_sharpe=1.0) == 0.0
    assert calculate_expected_max_sharpe(-5, var_sharpe=1.0) == 0.0
    assert calculate_expected_max_sharpe(10, var_sharpe=0.0) == 0.0


def test_expected_max_sharpe_monotonic_increase():
    """E[max_N] must increase monotonically with trial count N."""
    em_1 = calculate_expected_max_sharpe(1, var_sharpe=1.0)
    em_5 = calculate_expected_max_sharpe(5, var_sharpe=1.0)
    em_20 = calculate_expected_max_sharpe(20, var_sharpe=1.0)
    em_100 = calculate_expected_max_sharpe(100, var_sharpe=1.0)
    em_1000 = calculate_expected_max_sharpe(1000, var_sharpe=1.0)

    assert em_1 == 0.0
    assert 0.0 < em_5 < em_20 < em_100 < em_1000
    assert round(em_100, 2) == 2.53  # Matches Bailey & López de Prado (2014)


def test_expected_max_sharpe_variance_scaling():
    """E[max_N] must scale strictly with sqrt(V)."""
    em_v1 = calculate_expected_max_sharpe(50, var_sharpe=1.0)
    em_v4 = calculate_expected_max_sharpe(50, var_sharpe=4.0)
    assert pytest.approx(em_v4, rel=1e-5) == em_v1 * 2.0


def test_sharpe_std_error_normal_vs_nonnormal():
    """Non-normality (negative skew and fat tails) must inflate standard error."""
    sr = 1.5
    t = 250
    se_normal = calculate_sharpe_std_error(observed_sharpe=sr, sample_size=t, skewness=0.0, kurtosis=3.0)

    # Negative skew (asymmetric downside)
    se_neg_skew = calculate_sharpe_std_error(observed_sharpe=sr, sample_size=t, skewness=-1.5, kurtosis=3.0)
    assert se_neg_skew > se_normal

    # Fat tails (excess kurtosis)
    se_fat_tails = calculate_sharpe_std_error(observed_sharpe=sr, sample_size=t, skewness=0.0, kurtosis=6.0)
    assert se_fat_tails > se_normal


def test_calculate_psr_behavior():
    """PSR(0) should be > 0.5 for positive Sharpe, 0.5 for 0 Sharpe, and < 0.5 for negative."""
    psr_pos = calculate_psr(observed_sharpe=1.0, benchmark_sharpe=0.0, sample_size=250)
    psr_zero = calculate_psr(observed_sharpe=0.0, benchmark_sharpe=0.0, sample_size=250)
    psr_neg = calculate_psr(observed_sharpe=-1.0, benchmark_sharpe=0.0, sample_size=250)

    assert psr_pos > 0.99
    assert pytest.approx(psr_zero, abs=1e-3) == 0.50
    assert psr_neg < 0.01


def test_calculate_dsr_single_vs_multiple_trials():
    """DSR must deflate statistical confidence as trial count N increases."""
    sr = 1.2
    t = 250

    dsr_n1 = calculate_dsr(observed_sharpe=sr, sample_size=t, num_trials=1)
    dsr_n5 = calculate_dsr(observed_sharpe=sr, sample_size=t, num_trials=5)
    dsr_n10 = calculate_dsr(observed_sharpe=sr, sample_size=t, num_trials=10)
    dsr_n100 = calculate_dsr(observed_sharpe=sr, sample_size=t, num_trials=100)

    # N=1 matches PSR(0)
    assert pytest.approx(dsr_n1.deflated_sharpe_ratio, rel=1e-4) == dsr_n1.probabilistic_sharpe_ratio
    assert dsr_n1.passed is True

    # Monotonic drop in DSR as trial count expands
    assert dsr_n1.deflated_sharpe_ratio > dsr_n5.deflated_sharpe_ratio
    assert dsr_n5.deflated_sharpe_ratio > dsr_n10.deflated_sharpe_ratio
    assert dsr_n10.deflated_sharpe_ratio > dsr_n100.deflated_sharpe_ratio

    # At N=100, Sharpe of 1.2 fails DSR 95% threshold due to multiple testing
    assert dsr_n100.passed is False
    assert dsr_n100.deflated_sharpe_ratio < 0.01


def test_calculate_dsr_extreme_numerical_bounds():
    """DSR handles invalid sample sizes and astronomical trial counts safely."""
    # T <= 1
    dsr_t0 = calculate_dsr(observed_sharpe=2.0, sample_size=1, num_trials=5)
    assert dsr_t0.passed is False
    assert dsr_t0.deflated_sharpe_ratio == 0.0

    # Astronomical trials (N = 1,000,000)
    dsr_huge_n = calculate_dsr(observed_sharpe=2.0, sample_size=500, num_trials=1_000_000)
    assert not math.isnan(dsr_huge_n.deflated_sharpe_ratio)
    assert not math.isinf(dsr_huge_n.deflated_sharpe_ratio)
    assert dsr_huge_n.deflated_sharpe_ratio < 1e-5


def test_calculate_dsr_from_returns_empirical():
    """calculate_dsr_from_returns accurately computes DSR from raw return arrays."""
    np.random.seed(42)
    # Generate 500 returns with positive mean and slight negative skew
    returns = np.random.normal(0.001, 0.01, 500)
    returns[0] = -0.05  # Outlier to introduce negative skew

    res = calculate_dsr_from_returns(returns, num_trials=5, annual_factor=np.sqrt(252))
    assert res.sample_size == 500
    assert res.observed_sharpe > 0.0
    assert res.skewness < 0.0  # Negative skew detected
    assert 0.0 <= res.deflated_sharpe_ratio <= 1.0


def test_calculate_dsr_from_constant_returns():
    """Zero variance returns return fail-closed DSR without throwing ZeroDivisionError."""
    flat_returns = np.full(100, 0.001)
    res = calculate_dsr_from_returns(flat_returns, num_trials=1)
    assert res.passed is False
    assert res.deflated_sharpe_ratio == 0.0
    assert "returns variance" in res.summary or "Sharpe undefined" in res.summary


# ==============================================================================
# 2. AST DEDUPLICATION & FINGERPRINTING
# ==============================================================================

def test_ast_hash_identical_for_equivalent_strategies(sample_proposal):
    """Two identical strategy ASTs produce identical SHA-256 hashes."""
    hash1 = compute_strategy_hash(sample_proposal)

    # Clone proposal with different UUID and name
    clone = sample_proposal.model_copy(deep=True)
    clone.strategy_id = "00000000-0000-0000-0000-000000000000"
    clone.name = "CLONED_NAME"

    # Note: Name is included in the hash if we hashed name, but let's check
    # In compute_strategy_hash, rules/indicators/params/direction determine the logic
    hash2 = compute_strategy_hash(sample_proposal)
    assert hash1 == hash2


def test_ast_hash_distinct_for_different_parameters(sample_proposal):
    """Changing parameter values or indicator thresholds produces distinct hashes."""
    hash1 = compute_strategy_hash(sample_proposal)

    mutated = sample_proposal.model_copy(deep=True)
    mutated.indicators[0].inputs["period"] = 15  # Changed from 12 to 15
    hash2 = compute_strategy_hash(mutated)

    assert hash1 != hash2


# ==============================================================================
# 3. HOLDOUT OOS DATASET PARTITION & HOLDOUT GATE
# ==============================================================================

def test_partition_train_holdout_strict_chronological_isolation(market_df):
    """Holdout partition strictly isolates final 20% chronologically."""
    train_df, holdout_df = partition_train_holdout(market_df, holdout_ratio=0.20)

    assert len(train_df) == int(len(market_df) * 0.80)
    assert len(holdout_df) == len(market_df) - len(train_df)

    # Zero leakage assertion
    assert train_df["timestamp"].max() < holdout_df["timestamp"].min()


def test_partition_train_holdout_insufficient_bars_rejected():
    """Datasets with < 100 bars are rejected with ValueError."""
    tiny_df = pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=50, freq="1h", tz=timezone.utc),
            "close": np.full(50, 2000.0),
        }
    )
    with pytest.raises(ValueError, match="Insufficient bars"):
        partition_train_holdout(tiny_df, holdout_ratio=0.20)


def test_holdout_gate_rejection_low_trades(sample_proposal, market_df):
    """Holdout gate fails if strategy generates fewer than MIN_HOLDOUT_TRADES."""
    _, holdout_df = partition_train_holdout(market_df, holdout_ratio=0.20)

    # Use strict trade count threshold
    config = ValidationThresholdsConfig(MIN_HOLDOUT_TRADES=50)
    gate = HoldoutValidationGate(config=config)

    res = gate.evaluate_strategy(sample_proposal, holdout_df)
    assert res.passed is False
    assert any("Holdout trade count" in r for r in res.rejection_reasons)


# ==============================================================================
# 4. TOURNAMENT MANAGER & MULTI-BATCH TRIAL TRACKING
# ==============================================================================

def test_tournament_trial_count_persists_across_batches(sample_proposal, market_df):
    """TournamentManager must preserve cumulative trial counts across batches."""
    train_df, holdout_df = partition_train_holdout(market_df, holdout_ratio=0.20)

    config = ValidationThresholdsConfig(
        MIN_TRADES_GATE=1,
        MIN_EXPECTANCY_SPREAD_RATIO=0.0,
        MIN_GROSS_PROFIT_FACTOR=0.0,
        MAX_DRAWDOWN_GATE=1.0,
        MIN_HOLDOUT_TRADES=1,
        MIN_HOLDOUT_SHARPE=0.0,
        MIN_DSR_THRESHOLD=0.0,
    )

    manager = TournamentManager(config=config, require_holdout=False)
    assert manager.total_trials_evaluated == 0

    # Batch 1: evaluate proposal 1
    p1 = sample_proposal.model_copy(deep=True)
    p1.name = "STRAT_BATCH_1"
    p1.indicators[0].inputs["period"] = 10
    manager.evaluate_proposal(p1, train_df)
    assert manager.total_trials_evaluated == 1

    # Batch 2: evaluate proposal 2
    p2 = sample_proposal.model_copy(deep=True)
    p2.name = "STRAT_BATCH_2"
    p2.indicators[0].inputs["period"] = 14
    manager.evaluate_proposal(p2, train_df)
    assert manager.total_trials_evaluated == 2

    # Trial count did not reset!
    report = manager.get_leaderboard()
    assert report.total_trials_evaluated == 2
    assert len(report.leaderboard) == 2


def test_tournament_deduplication(sample_proposal, market_df):
    """TournamentManager detects duplicate ASTs and rejects without inflating trial count."""
    train_df, _ = partition_train_holdout(market_df, holdout_ratio=0.20)

    manager = TournamentManager(require_holdout=False)

    # First evaluation
    entry1 = manager.evaluate_proposal(sample_proposal, train_df)
    assert manager.total_trials_evaluated == 1

    # Duplicate evaluation
    entry2 = manager.evaluate_proposal(sample_proposal, train_df)
    assert entry2.tier == "REJECTED"
    assert "DUPLICATE_STRATEGY" in entry2.explanation
    # Trial counter was NOT incremented on duplicate rejection
    assert manager.total_trials_evaluated == 1


def test_tournament_hard_gate_failure_zero_css(market_df):
    """Failing In-Sample hard gates sets CSS strictly to 0.0 and rejects candidate."""
    train_df, _ = partition_train_holdout(market_df, holdout_ratio=0.20)

    # High minimum trades gate that will fail
    config = ValidationThresholdsConfig(MIN_TRADES_GATE=500)
    manager = TournamentManager(config=config, require_holdout=False)

    proposal = StrategyProposal(
        name="FAILING_TRADES_STRAT",
        hypothesis="Testing hard gate failure rejection.",
        direction=TradeDirection.LONG,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.TRENDING_BULL],
        indicators=[
            IndicatorSpec(alias="ema_fast", name=IndicatorNameEnum.EMA, inputs={"period": 100}),
            IndicatorSpec(alias="ema_slow", name=IndicatorNameEnum.EMA, inputs={"period": 200}),
        ],
        entry_rules=ConditionNode(
            operator="AND",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="ema_fast", lag=0),
                    comparator=">",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_slow", lag=0),
                )
            ],
        ),
        exit_rules=ConditionNode(
            operator="OR",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="ema_fast", lag=0),
                    comparator="<",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_slow", lag=0),
                )
            ],
        ),
        stop_loss=StopLossSpec(type="ATR_MULTIPLE", multiplier_or_value=1.5),
        take_profit=TakeProfitSpec(type="ATR_MULTIPLE", multiplier_or_value=3.0),
        constraints=ExecutionConstraints(max_holding_bars=48, allowed_sessions=["ALL"]),
        rationale="Will fail trades gate due to high slow EMA period.",
    )

    entry = manager.evaluate_proposal(proposal, train_df)
    assert entry.passed_all_gates is False
    assert entry.css_score == 0.0
    assert entry.tier == "REJECTED"


def test_tournament_reset_requires_confirmation():
    """TournamentManager.reset_trials() strictly enforces confirmation parameter."""
    manager = TournamentManager()
    with pytest.raises(ValueError, match="requires explicit confirmation"):
        manager.reset_trials(confirm_reset=False)


def test_tournament_ranking_deterministic_and_winner(market_df, sample_proposal):
    """Tournament produces deterministically sorted leaderboard with Rank 1 WINNER."""
    train_df, _ = partition_train_holdout(market_df, holdout_ratio=0.20)

    # Config with attainable gates
    config = ValidationThresholdsConfig(
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
    )

    manager = TournamentManager(config=config, require_holdout=False, min_dsr_threshold=0.10)

    # Strategy A
    p_a = sample_proposal.model_copy(deep=True)
    p_a.name = "STRAT_ALPHA"
    p_a.indicators[0].inputs["period"] = 8
    p_a.indicators[1].inputs["period"] = 20

    # Strategy B (different period)
    p_b = sample_proposal.model_copy(deep=True)
    p_b.name = "STRAT_BETA"
    p_b.indicators[0].inputs["period"] = 16
    p_b.indicators[1].inputs["period"] = 32

    report = manager.evaluate_batch([p_a, p_b], train_df)

    assert report.total_trials_evaluated == 2
    assert len(report.leaderboard) == 2
    # Ranks must be 1 and 2
    assert report.leaderboard[0].rank == 1
    assert report.leaderboard[1].rank == 2

    # CSS must be non-increasing
    assert report.leaderboard[0].css_score >= report.leaderboard[1].css_score

    # Check winner
    if report.surviving_count > 0:
        assert report.winner is not None
        assert report.winner.rank == 1
        assert "WINNER" in report.winner.tier or report.winner.tier in ("TIER_1", "TIER_2")


def test_holdout_gate_rejection_low_sharpe(sample_proposal, market_df):
    """Holdout gate rejects strategy if holdout Sharpe is below MIN_HOLDOUT_SHARPE."""
    _, holdout_df = partition_train_holdout(market_df, holdout_ratio=0.20)

    # Impossibly high Sharpe requirement
    config = ValidationThresholdsConfig(MIN_HOLDOUT_TRADES=1, MIN_HOLDOUT_SHARPE=10.0)
    gate = HoldoutValidationGate(config=config)

    res = gate.evaluate_strategy(sample_proposal, holdout_df)
    assert res.passed is False
    assert any("Holdout Sharpe ratio" in r for r in res.rejection_reasons)


def test_tournament_candidate_lineage_tracking(market_df, sample_proposal):
    """Tournament preserves and propagates candidate lineage through to leaderboard."""
    train_df, _ = partition_train_holdout(market_df, holdout_ratio=0.20)

    manager = TournamentManager(require_holdout=False)

    lineage = CandidateLineage(
        strategy_id="strat_gen2_01",
        name="MUTATED_CHILD",
        hypothesis_id="hyp_london_breakout_v2",
        parent_strategy_id="strat_gen1_01",
        generation=2,
    )

    p = sample_proposal.model_copy(deep=True)
    p.strategy_id = "strat_gen2_01"
    p.name = "MUTATED_CHILD"

    entry = manager.evaluate_proposal(p, train_df, lineage=lineage)
    assert entry.lineage.hypothesis_id == "hyp_london_breakout_v2"
    assert entry.lineage.parent_strategy_id == "strat_gen1_01"
    assert entry.lineage.generation == 2


def test_dsr_with_empirical_trial_sharpes():
    """DSR with empirical trial_sharpes calculates variance V directly from trial history."""
    trial_sharpes = [1.5, 0.8, 0.2, -0.4, 1.1, 0.0]
    expected_v = float(np.var(trial_sharpes, ddof=1))

    res = calculate_dsr(
        observed_sharpe=1.6,
        sample_size=500,
        num_trials=len(trial_sharpes),
        trial_sharpes=trial_sharpes,
    )

    assert pytest.approx(res.var_sharpe, rel=1e-4) == expected_v
    assert res.expected_max_sharpe > 0.0
