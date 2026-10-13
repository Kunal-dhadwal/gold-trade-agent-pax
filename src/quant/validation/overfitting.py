"""Quantitative overfitting controls, Deflated Sharpe Ratio (DSR), and Strategy Tournament Manager.

Governing specifications:
- ADR-008: Anti-Overfitting Architecture, OOS Quarantine & Configurable Validation Gates
- ADR-010: Composite Strategy Scoring & Quantitative Tiering
- Bailey & López de Prado (2014): The Deflated Sharpe Ratio
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import numpy as np
import pandas as pd
import scipy.stats as stats

from src.backtest.engine import BacktestEngine
from src.backtest.types import BacktestConfig, BacktestMetrics, BacktestResult
from src.core.logger import logger
from src.quant.scoring import CompositeStrategyScorer, StrategyScoreResult
from src.quant.validation.config import DEFAULT_VALIDATION_CONFIG, ValidationThresholdsConfig
from src.quant.validation.pipeline import CascadingValidationPipeline, ValidationPipelineReport
from src.strategy.contracts import StrategyProposal
from src.strategy.interpreter import ASTStrategy


# ==============================================================================
# DATA STRUCTURES
# ==============================================================================

@dataclass
class DSRResult:
    """Statistical outcome of Deflated Sharpe Ratio (DSR) calculation."""
    observed_sharpe: float
    deflated_sharpe_ratio: float  # DSR in [0.0, 1.0]
    probabilistic_sharpe_ratio: float  # PSR(0) in [0.0, 1.0]
    expected_max_sharpe: float  # E[max_N] under H0: true SR = 0
    standard_error: float  # sigma_SR under non-normality
    sample_size: int  # T (number of return observations)
    skewness: float  # gamma_3
    kurtosis: float  # gamma_4 (Pearson kurtosis, normal = 3.0)
    num_trials: int  # N (number of independent or cumulative trials)
    var_sharpe: float  # Variance of trials V
    z_score: float  # z = (observed_sharpe - expected_max_sharpe) / standard_error
    passed: bool  # DSR >= min_threshold
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observed_sharpe": round(self.observed_sharpe, 4),
            "deflated_sharpe_ratio": round(self.deflated_sharpe_ratio, 4),
            "probabilistic_sharpe_ratio": round(self.probabilistic_sharpe_ratio, 4),
            "expected_max_sharpe": round(self.expected_max_sharpe, 4),
            "standard_error": round(self.standard_error, 4),
            "sample_size": self.sample_size,
            "skewness": round(self.skewness, 4),
            "kurtosis": round(self.kurtosis, 4),
            "num_trials": self.num_trials,
            "var_sharpe": round(self.var_sharpe, 4),
            "z_score": round(self.z_score, 4),
            "passed": self.passed,
            "summary": self.summary,
        }


@dataclass
class HoldoutValidationResult:
    """Outcome of terminal evaluation on the quarantined 20% holdout partition."""
    passed: bool
    sharpe_ratio: float
    profit_factor: float
    max_drawdown_pct: float
    total_return_pct: float
    total_trades: int
    win_rate_pct: float
    rejection_reasons: List[str]
    holdout_start: datetime
    holdout_end: datetime
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "profit_factor": round(self.profit_factor, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "total_return_pct": round(self.total_return_pct, 4),
            "total_trades": self.total_trades,
            "win_rate_pct": round(self.win_rate_pct, 4),
            "rejection_reasons": self.rejection_reasons,
            "holdout_start": self.holdout_start.isoformat(),
            "holdout_end": self.holdout_end.isoformat(),
            "summary": self.summary,
        }


@dataclass
class CandidateLineage:
    """Tracks strategy generation lineage, parent mutations, and hypothesis links."""
    strategy_id: str
    name: str
    hypothesis_id: Optional[str] = None
    parent_strategy_id: Optional[str] = None
    generation: int = 1
    trial_index: int = 1
    ast_hash: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "hypothesis_id": self.hypothesis_id,
            "parent_strategy_id": self.parent_strategy_id,
            "generation": self.generation,
            "trial_index": self.trial_index,
            "ast_hash": self.ast_hash,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class TournamentLeaderboardEntry:
    """Ranked strategy candidate record with complete evidence and explanation."""
    rank: int
    strategy_id: str
    strategy_name: str
    css_score: float
    dsr_score: float
    tier: str  # "TIER_1_WINNER", "TIER_1", "TIER_2", "DISQUALIFIED", "REJECTED"
    passed_all_gates: bool
    sortino_ratio: float
    profit_factor: float
    max_drawdown_pct: float
    wfe_ratio: float
    monte_carlo_ruin_prob: float
    holdout_sharpe: Optional[float]
    holdout_passed: Optional[bool]
    trials_count: int
    expected_max_sharpe: float
    lineage: CandidateLineage
    rejection_reasons: List[str]
    explanation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "css_score": round(self.css_score, 2),
            "dsr_score": round(self.dsr_score, 4),
            "tier": self.tier,
            "passed_all_gates": self.passed_all_gates,
            "sortino_ratio": round(self.sortino_ratio, 4),
            "profit_factor": round(self.profit_factor, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "wfe_ratio": round(self.wfe_ratio, 4),
            "monte_carlo_ruin_prob": round(self.monte_carlo_ruin_prob, 4),
            "holdout_sharpe": round(self.holdout_sharpe, 4) if self.holdout_sharpe is not None else None,
            "holdout_passed": self.holdout_passed,
            "trials_count": self.trials_count,
            "expected_max_sharpe": round(self.expected_max_sharpe, 4),
            "lineage": self.lineage.to_dict(),
            "rejection_reasons": self.rejection_reasons,
            "explanation": self.explanation,
        }


@dataclass
class TournamentReport:
    """Comprehensive tournament outcome report."""
    total_trials_evaluated: int
    unique_strategies_count: int
    surviving_count: int
    rejected_count: int
    winner: Optional[TournamentLeaderboardEntry]
    leaderboard: List[TournamentLeaderboardEntry]
    trial_sharpes_summary: Dict[str, float]
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_trials_evaluated": self.total_trials_evaluated,
            "unique_strategies_count": self.unique_strategies_count,
            "surviving_count": self.surviving_count,
            "rejected_count": self.rejected_count,
            "winner": self.winner.to_dict() if self.winner else None,
            "leaderboard": [entry.to_dict() for entry in self.leaderboard],
            "trial_sharpes_summary": self.trial_sharpes_summary,
            "generated_at": self.generated_at.isoformat(),
        }


# ==============================================================================
# STATISTICAL OVERFITTING CALCULATIONS (DSR & PSR)
# ==============================================================================

def calculate_expected_max_sharpe(num_trials: int, var_sharpe: float = 1.0) -> float:
    """Calculate expected maximum Sharpe ratio under the null hypothesis (Bailey & López de Prado 2014).

    E[max_N] = sqrt(V) * ((1 - gamma) * Z^{-1}(1 - 1/N) + gamma * Z^{-1}(1 - 1/(N * e)))
    where gamma is Euler-Mascheroni constant (~0.5772156649).
    """
    if num_trials <= 1 or var_sharpe <= 0.0:
        return 0.0

    euler_mascheroni = 0.5772156649015328606
    n = max(float(num_trials), 1.0)

    # Clamping bounds to prevent numerical infinity at extremes
    p1 = min(max(1.0 - (1.0 / n), 1e-15), 1.0 - 1e-15)
    p2 = min(max(1.0 - (1.0 / (n * math.e)), 1e-15), 1.0 - 1e-15)

    z1 = float(stats.norm.ppf(p1))
    z2 = float(stats.norm.ppf(p2))

    expected_max = math.sqrt(var_sharpe) * ((1.0 - euler_mascheroni) * z1 + euler_mascheroni * z2)
    return max(0.0, float(expected_max))


def calculate_sharpe_std_error(
    observed_sharpe: float,
    sample_size: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Calculate asymptotic standard error of Sharpe ratio under non-normality (Mertens 2002 / Lo 2002).

    sigma_SR = sqrt( (1 - gamma_3 * SR + ((gamma_4 - 1) / 4) * SR^2) / (T - 1) )
    where:
      gamma_3 = skewness
      gamma_4 = Pearson kurtosis (normal distribution = 3.0)
      T = sample_size
    """
    if sample_size <= 1:
        return 0.0

    # Ensure Pearson kurtosis is physically valid (kurtosis >= 1.0 mathematically)
    kurt = max(float(kurtosis), 1.0)
    sr = float(observed_sharpe)

    numerator = 1.0 - (skewness * sr) + (((kurt - 1.0) / 4.0) * (sr ** 2))
    if numerator <= 0.0:
        numerator = 1e-12

    variance_sr = numerator / max(float(sample_size - 1), 1.0)
    return math.sqrt(max(variance_sr, 1e-12))


def calculate_psr(
    observed_sharpe: float,
    benchmark_sharpe: float = 0.0,
    sample_size: int = 250,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Calculate Probabilistic Sharpe Ratio (PSR) relative to a benchmark (Bailey & López de Prado 2012).

    PSR(SR*) = Phi( (SR - SR*) / sigma_SR )
    """
    if sample_size <= 1:
        return 0.0

    se = calculate_sharpe_std_error(observed_sharpe, sample_size, skewness, kurtosis)
    if se <= 0.0:
        return 0.0

    z = (observed_sharpe - benchmark_sharpe) / se
    return float(stats.norm.cdf(z))


def calculate_dsr(
    observed_sharpe: float,
    sample_size: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    num_trials: int = 1,
    var_sharpe: Optional[float] = None,
    trial_sharpes: Optional[Sequence[float]] = None,
    min_dsr_threshold: float = 0.95,
) -> DSRResult:
    """Calculate Deflated Sharpe Ratio (DSR) correcting for selection bias and multiple testing.

    DSR = PSR(E[max_N]) = Phi( (SR - E[max_N]) / sigma_SR )
    """
    if sample_size <= 1:
        return DSRResult(
            observed_sharpe=observed_sharpe,
            deflated_sharpe_ratio=0.0,
            probabilistic_sharpe_ratio=0.0,
            expected_max_sharpe=0.0,
            standard_error=0.0,
            sample_size=sample_size,
            skewness=skewness,
            kurtosis=kurtosis,
            num_trials=max(1, num_trials),
            var_sharpe=0.0,
            z_score=0.0,
            passed=False,
            summary="Invalid sample size (T <= 1). DSR cannot be evaluated.",
        )

    # Determine empirical variance of trials V
    if trial_sharpes is not None and len(trial_sharpes) > 1:
        v = float(np.var(trial_sharpes, ddof=1))
    elif var_sharpe is not None:
        v = max(0.0, float(var_sharpe))
    else:
        v = 1.0 if num_trials > 1 else 0.0

    e_max = calculate_expected_max_sharpe(num_trials, v)
    se = calculate_sharpe_std_error(observed_sharpe, sample_size, skewness, kurtosis)

    if se <= 0.0 or math.isnan(se):
        z = 0.0
        dsr = 0.0
        psr = 0.0
    else:
        z = (observed_sharpe - e_max) / se
        dsr = 0.0 if math.isnan(z) else float(stats.norm.cdf(z))
        psr = 0.0 if math.isnan(z) else float(stats.norm.cdf(observed_sharpe / se))

    passed = (dsr >= min_dsr_threshold) and (sample_size > 1) and not math.isnan(dsr)

    if passed:
        summary = (
            f"DSR Passed ({dsr:.4f} >= {min_dsr_threshold:.2f}). "
            f"Observed Sharpe {observed_sharpe:.2f} statistically significant after {num_trials} trials "
            f"(E[max]={e_max:.2f}, SE={se:.4f})."
        )
    else:
        summary = (
            f"DSR Failed ({dsr:.4f} < {min_dsr_threshold:.2f}). "
            f"Observed Sharpe {observed_sharpe:.2f} insufficient to reject multiple-testing bias after {num_trials} trials "
            f"(E[max]={e_max:.2f}, SE={se:.4f})."
        )

    return DSRResult(
        observed_sharpe=observed_sharpe,
        deflated_sharpe_ratio=0.0 if math.isnan(dsr) else dsr,
        probabilistic_sharpe_ratio=0.0 if math.isnan(psr) else psr,
        expected_max_sharpe=e_max,
        standard_error=0.0 if math.isnan(se) else se,
        sample_size=sample_size,
        skewness=0.0 if math.isnan(skewness) else skewness,
        kurtosis=3.0 if math.isnan(kurtosis) else kurtosis,
        num_trials=max(1, num_trials),
        var_sharpe=v,
        z_score=0.0 if math.isnan(z) else z,
        passed=passed,
        summary=summary,
    )


def calculate_dsr_from_returns(
    returns: Union[pd.Series, np.ndarray, Sequence[float]],
    num_trials: int = 1,
    trial_sharpes: Optional[Sequence[float]] = None,
    var_sharpe: Optional[float] = None,
    annual_factor: float = 1.0,
    min_dsr_threshold: float = 0.95,
) -> DSRResult:
    """Calculate DSR directly from an empirical returns series (bar-by-bar or trade-by-trade)."""
    ret_arr = np.asarray(returns, dtype=float)
    ret_clean = ret_arr[np.isfinite(ret_arr)]

    n = len(ret_clean)
    if n < 2:
        return DSRResult(
            observed_sharpe=0.0,
            deflated_sharpe_ratio=0.0,
            probabilistic_sharpe_ratio=0.0,
            expected_max_sharpe=0.0,
            standard_error=0.0,
            sample_size=n,
            skewness=0.0,
            kurtosis=3.0,
            num_trials=max(1, num_trials),
            var_sharpe=0.0,
            z_score=0.0,
            passed=False,
            summary="Insufficient return observations (N < 2).",
        )

    mean_ret = float(np.mean(ret_clean))
    std_ret = float(np.std(ret_clean, ddof=1))

    if std_ret <= 1e-12 or math.isnan(std_ret):
        return DSRResult(
            observed_sharpe=0.0,
            deflated_sharpe_ratio=0.0,
            probabilistic_sharpe_ratio=0.0,
            expected_max_sharpe=0.0,
            standard_error=0.0,
            sample_size=n,
            skewness=0.0,
            kurtosis=3.0,
            num_trials=max(1, num_trials),
            var_sharpe=0.0,
            z_score=0.0,
            passed=False,
            summary="Zero or near-zero returns variance. Sharpe undefined.",
        )

    period_sharpe = mean_ret / std_ret
    ann_sharpe = period_sharpe * annual_factor

    # Skewness and Pearson kurtosis
    skew = float(stats.skew(ret_clean, bias=False)) if n >= 3 else 0.0
    # Pearson kurtosis (normal = 3.0; fisher=False gives Pearson)
    kurt = float(stats.kurtosis(ret_clean, fisher=False, bias=False)) if n >= 4 else 3.0

    return calculate_dsr(
        observed_sharpe=ann_sharpe,
        sample_size=n,
        skewness=skew,
        kurtosis=kurt,
        num_trials=num_trials,
        var_sharpe=var_sharpe,
        trial_sharpes=trial_sharpes,
        min_dsr_threshold=min_dsr_threshold,
    )


# ==============================================================================
# AST DEDUPLICATION & FINGERPRINTING
# ==============================================================================

def compute_strategy_hash(proposal: StrategyProposal) -> str:
    """Compute deterministic cryptographic SHA-256 fingerprint for strategy AST rules.

    Eliminates superficial differences (UUIDs, timestamps) and identifies duplicate
    parameter permutations or condition trees.
    """
    canonical_payload = {
        "instrument": proposal.instrument.value,
        "timeframe": proposal.timeframe.value,
        "direction": proposal.direction.value,
        "indicators": [
            {
                "alias": ind.alias,
                "name": ind.name.value,
                "inputs": getattr(ind, "inputs", {}),
            }
            for ind in sorted(proposal.indicators, key=lambda x: x.alias)
        ],
        "entry_rules": proposal.entry_rules.model_dump(),
        "exit_rules": proposal.exit_rules.model_dump(),
        "stop_loss": proposal.stop_loss.model_dump(),
        "take_profit": proposal.take_profit.model_dump(),
        "parameters": [
            {
                "name": p.name,
                "default": p.default_value,
                "min": p.min_value,
                "max": p.max_value,
            }
            for p in sorted(proposal.parameters, key=lambda x: x.name)
        ],
    }
    encoded = json.dumps(canonical_payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ==============================================================================
# HOLDOUT OOS DATASET PARTITION & HOLDOUT GATE
# ==============================================================================

def partition_train_holdout(
    df: pd.DataFrame,
    holdout_ratio: float = 0.20,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Strictly partition historical dataset into Train/In-Sample (80%) and Quarantined Holdout (20%).

    ARCHITECTURAL SECURITY INVARIANT:
    The final holdout_ratio (default 20%) is strictly chronological and isolated.
    Holdout bars must NEVER be visible during ideation, parameter tuning, or WFA.
    """
    if "timestamp" not in df.columns:
        raise ValueError("DataFrame must contain 'timestamp' column for chronological partitioning.")

    ratio = min(max(float(holdout_ratio), 0.05), 0.50)
    df_sorted = df.sort_values("timestamp").reset_index(drop=True)

    n_total = len(df_sorted)
    if n_total < 100:
        raise ValueError(f"Insufficient bars ({n_total} < 100) to create holdout partition.")

    split_idx = int(n_total * (1.0 - ratio))
    train_df = df_sorted.iloc[:split_idx].copy().reset_index(drop=True)
    holdout_df = df_sorted.iloc[split_idx:].copy().reset_index(drop=True)

    # Invariant assertion: absolute chronological separation
    max_train_ts = train_df["timestamp"].max()
    min_holdout_ts = holdout_df["timestamp"].min()

    if max_train_ts >= min_holdout_ts:
        raise ValueError(
            f"Holdout partition leak! Max train ts ({max_train_ts}) >= Min holdout ts ({min_holdout_ts})."
        )

    return train_df, holdout_df


class HoldoutValidationGate:
    """Terminal gatekeeper evaluating candidate on the final air-gapped 20% holdout partition."""

    def __init__(
        self,
        config: Optional[ValidationThresholdsConfig] = None,
        backtest_config: Optional[BacktestConfig] = None,
    ):
        self.config = config or DEFAULT_VALIDATION_CONFIG
        self.backtest_config = backtest_config or BacktestConfig()
        self.engine = BacktestEngine(self.backtest_config)

    def evaluate_strategy(
        self,
        proposal: StrategyProposal,
        holdout_df: pd.DataFrame,
    ) -> HoldoutValidationResult:
        """Run terminal forward evaluation on holdout data with zero prior leakage."""
        if holdout_df.empty:
            return HoldoutValidationResult(
                passed=False,
                sharpe_ratio=0.0,
                profit_factor=0.0,
                max_drawdown_pct=0.0,
                total_return_pct=0.0,
                total_trades=0,
                win_rate_pct=0.0,
                rejection_reasons=["Empty holdout dataset provided."],
                holdout_start=datetime.now(timezone.utc),
                holdout_end=datetime.now(timezone.utc),
                summary="Failed: Holdout dataset is empty.",
            )

        strategy = ASTStrategy(proposal=proposal)
        inst = proposal.instrument.value
        tf = proposal.timeframe.value

        res = self.engine.run(holdout_df, strategy, instrument=inst, timeframe=tf)
        metrics = res.metrics

        reasons = []
        passed = True

        # Gate 1: Minimum holdout trades
        if metrics.total_trades < self.config.MIN_HOLDOUT_TRADES:
            passed = False
            reasons.append(
                f"Holdout trade count {metrics.total_trades} < minimum gate {self.config.MIN_HOLDOUT_TRADES}."
            )

        # Gate 2: Minimum holdout Sharpe ratio
        if metrics.sharpe_ratio < self.config.MIN_HOLDOUT_SHARPE:
            passed = False
            reasons.append(
                f"Holdout Sharpe ratio {metrics.sharpe_ratio:.2f} < minimum gate {self.config.MIN_HOLDOUT_SHARPE:.2f}."
            )

        # Gate 3: Maximum holdout peak-to-trough drawdown
        max_dd_limit = self.config.MAX_HOLDOUT_DRAWDOWN * 100.0
        if metrics.max_drawdown_pct > max_dd_limit:
            passed = False
            reasons.append(
                f"Holdout Max Drawdown {metrics.max_drawdown_pct:.2f}% > limit {max_dd_limit:.1f}%."
            )

        # Gate 4: Non-negative net profit
        if metrics.total_net_profit <= 0.0:
            passed = False
            reasons.append(
                f"Holdout Net Profit ${metrics.total_net_profit:.2f} <= $0.00."
            )

        start_ts = holdout_df["timestamp"].min()
        end_ts = holdout_df["timestamp"].max()

        if passed:
            summary = (
                f"Holdout Gate PASSED: Sharpe {metrics.sharpe_ratio:.2f}, "
                f"Trades {metrics.total_trades}, Net Profit ${metrics.total_net_profit:.2f}, "
                f"MaxDD {metrics.max_drawdown_pct:.2f}%."
            )
        else:
            summary = f"Holdout Gate FAILED: {'; '.join(reasons)}"

        return HoldoutValidationResult(
            passed=passed,
            sharpe_ratio=metrics.sharpe_ratio,
            profit_factor=metrics.profit_factor,
            max_drawdown_pct=metrics.max_drawdown_pct,
            total_return_pct=metrics.total_return_pct,
            total_trades=metrics.total_trades,
            win_rate_pct=metrics.win_rate_pct,
            rejection_reasons=reasons,
            holdout_start=start_ts,
            holdout_end=end_ts,
            summary=summary,
        )


# ==============================================================================
# TOURNAMENT MANAGER
# ==============================================================================

class TournamentManager:
    """Coordinates multi-candidate strategy tournaments, tracking trial budgets,
    enforcing cascading validation and DSR multiple-testing penalties, and ranking winners.
    """

    def __init__(
        self,
        config: Optional[ValidationThresholdsConfig] = None,
        backtest_config: Optional[BacktestConfig] = None,
        initial_trial_count: int = 0,
        min_dsr_threshold: float = 0.95,
        require_holdout: bool = True,
    ):
        self.config = config or DEFAULT_VALIDATION_CONFIG
        self.backtest_config = backtest_config or BacktestConfig()
        self.pipeline = CascadingValidationPipeline(self.config, self.backtest_config)
        self.scorer = CompositeStrategyScorer(self.config)
        self.holdout_gate = HoldoutValidationGate(self.config, self.backtest_config)

        # State tracking (NEVER silently reset between batches)
        self._total_trials_evaluated: int = max(0, initial_trial_count)
        self._submitted_hashes: Dict[str, str] = {}  # hash -> strategy_id
        self._evaluated_candidates: List[TournamentLeaderboardEntry] = []
        self._trial_sharpes: List[float] = []
        self.min_dsr_threshold = min_dsr_threshold
        self.require_holdout = require_holdout

    @property
    def total_trials_evaluated(self) -> int:
        """Total cumulative candidate evaluations across all batches."""
        return self._total_trials_evaluated

    @property
    def candidate_count(self) -> int:
        """Total registered candidate records."""
        return len(self._evaluated_candidates)

    def evaluate_proposal(
        self,
        proposal: StrategyProposal,
        train_df: pd.DataFrame,
        holdout_df: Optional[pd.DataFrame] = None,
        lineage: Optional[CandidateLineage] = None,
    ) -> TournamentLeaderboardEntry:
        """Evaluate a single proposal through cascading validation, DSR, and optional holdout."""
        ast_hash = compute_strategy_hash(proposal)
        strat_id = str(proposal.strategy_id)
        name = proposal.name

        # Construct lineage record if not supplied
        if lineage is None:
            lineage = CandidateLineage(
                strategy_id=strat_id,
                name=name,
                ast_hash=ast_hash,
                trial_index=self._total_trials_evaluated + 1,
            )
        else:
            lineage.ast_hash = ast_hash
            lineage.trial_index = self._total_trials_evaluated + 1

        # Check for AST duplicate
        if ast_hash in self._submitted_hashes:
            dup_id = self._submitted_hashes[ast_hash]
            reason = f"DUPLICATE_STRATEGY: Exact AST matches earlier candidate {dup_id}."
            entry = TournamentLeaderboardEntry(
                rank=0,
                strategy_id=strat_id,
                strategy_name=name,
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
                trials_count=self._total_trials_evaluated,
                expected_max_sharpe=0.0,
                lineage=lineage,
                rejection_reasons=[reason],
                explanation=f"Rejected without evaluation: {reason}",
            )
            self._evaluated_candidates.append(entry)
            return entry

        # Record hash and increment cumulative trial count
        self._submitted_hashes[ast_hash] = strat_id
        self._total_trials_evaluated += 1
        current_n = self._total_trials_evaluated

        # Step 1: Cascading Validation Pipeline on Train/IS dataset
        val_report = self.pipeline.run_pipeline(proposal, train_df)

        rejection_reasons = []
        is_metrics = val_report.is_backtest_result.metrics if val_report.is_backtest_result else None
        sortino = is_metrics.sortino_ratio if is_metrics else 0.0
        pf = is_metrics.profit_factor if is_metrics else 0.0
        dd = is_metrics.max_drawdown_pct if is_metrics else 0.0
        sharpe = is_metrics.sharpe_ratio if is_metrics else 0.0
        wfe = val_report.wfa_result.mean_wfe if val_report.wfa_result else 0.0
        mc_ruin = val_report.monte_carlo_result.probability_of_ruin if val_report.monte_carlo_result else 1.0

        self._trial_sharpes.append(sharpe)

        if not val_report.overall_passed:
            # Capture failure reasons from stages
            for stage in val_report.stages:
                if not stage.passed and stage.rejection_reason:
                    rejection_reasons.append(f"{stage.stage_name}: {stage.rejection_reason}")

            entry = TournamentLeaderboardEntry(
                rank=0,
                strategy_id=strat_id,
                strategy_name=name,
                css_score=0.0,
                dsr_score=0.0,
                tier="REJECTED",
                passed_all_gates=False,
                sortino_ratio=sortino,
                profit_factor=pf,
                max_drawdown_pct=dd,
                wfe_ratio=wfe,
                monte_carlo_ruin_prob=mc_ruin,
                holdout_sharpe=None,
                holdout_passed=None,
                trials_count=current_n,
                expected_max_sharpe=0.0,
                lineage=lineage,
                rejection_reasons=rejection_reasons,
                explanation=f"Rejected at validation pipeline: {'; '.join(rejection_reasons)}",
            )
            self._evaluated_candidates.append(entry)
            return entry

        # Step 2: DSR Overfitting Evaluation
        sample_size = len(val_report.is_backtest_result.equity_curve) if val_report.is_backtest_result else len(train_df)
        dsr_res = calculate_dsr(
            observed_sharpe=sharpe,
            sample_size=sample_size,
            skewness=0.0,
            kurtosis=3.0,
            num_trials=current_n,
            trial_sharpes=self._trial_sharpes,
            min_dsr_threshold=self.min_dsr_threshold,
        )

        if not dsr_res.passed:
            rejection_reasons.append(f"DSR_GATE: {dsr_res.summary}")

        # Step 3: Quarantined Holdout Gate (evaluated ONLY after passing Train/IS validation)
        holdout_passed = None
        holdout_sharpe = None

        if self.require_holdout and holdout_df is not None:
            holdout_res = self.holdout_gate.evaluate_strategy(proposal, holdout_df)
            holdout_passed = holdout_res.passed
            holdout_sharpe = holdout_res.sharpe_ratio

            if not holdout_passed:
                for reason in holdout_res.rejection_reasons:
                    rejection_reasons.append(f"HOLDOUT_GATE: {reason}")

        # Step 4: Overall Status & Tier Determination
        passed_all = (
            val_report.overall_passed
            and dsr_res.passed
            and (holdout_passed if (self.require_holdout and holdout_df is not None) else True)
        )

        raw_css = val_report.score_result.css_score if val_report.score_result else 0.0

        if not passed_all:
            final_css = 0.0
            tier = "DISQUALIFIED"
            explanation = f"Disqualified: {'; '.join(rejection_reasons)}"
        else:
            final_css = raw_css
            if final_css >= self.config.CSS_TIER1_THRESHOLD and dsr_res.passed:
                tier = "TIER_1"
            elif final_css >= self.config.CSS_TIER2_THRESHOLD:
                tier = "TIER_2"
            else:
                tier = "DISQUALIFIED"
            explanation = (
                f"Passed all gates. CSS: {final_css:.2f} ({tier}), "
                f"DSR: {dsr_res.deflated_sharpe_ratio * 100:.1f}% (N={current_n}), "
                f"Holdout Sharpe: {holdout_sharpe:.2f}."
                if holdout_sharpe is not None
                else f"Passed all gates. CSS: {final_css:.2f} ({tier}), DSR: {dsr_res.deflated_sharpe_ratio * 100:.1f}%."
            )

        entry = TournamentLeaderboardEntry(
            rank=0,
            strategy_id=strat_id,
            strategy_name=name,
            css_score=final_css,
            dsr_score=dsr_res.deflated_sharpe_ratio,
            tier=tier,
            passed_all_gates=passed_all,
            sortino_ratio=sortino,
            profit_factor=pf,
            max_drawdown_pct=dd,
            wfe_ratio=wfe,
            monte_carlo_ruin_prob=mc_ruin,
            holdout_sharpe=holdout_sharpe,
            holdout_passed=holdout_passed,
            trials_count=current_n,
            expected_max_sharpe=dsr_res.expected_max_sharpe,
            lineage=lineage,
            rejection_reasons=rejection_reasons,
            explanation=explanation,
        )
        self._evaluated_candidates.append(entry)
        return entry

    def evaluate_batch(
        self,
        proposals: List[StrategyProposal],
        train_df: pd.DataFrame,
        holdout_df: Optional[pd.DataFrame] = None,
        lineages: Optional[List[CandidateLineage]] = None,
    ) -> TournamentReport:
        """Evaluate a batch of candidate proposals and return a full tournament report."""
        for i, proposal in enumerate(proposals):
            lineage = lineages[i] if (lineages and i < len(lineages)) else None
            self.evaluate_proposal(proposal, train_df, holdout_df, lineage)

        return self.get_leaderboard()

    def get_leaderboard(self, tier_filter: Optional[str] = None) -> TournamentReport:
        """Generate a deterministically sorted tournament leaderboard with winner designation.

        Sorting order:
        1. passed_all_gates (True > False)
        2. css_score (descending)
        3. dsr_score (descending)
        4. sortino_ratio (descending)
        5. strategy_id (alphanumeric ascending tie-breaker)
        """
        candidates = list(self._evaluated_candidates)

        if tier_filter:
            candidates = [c for c in candidates if c.tier == tier_filter]

        # Multi-factor deterministic sort
        sorted_candidates = sorted(
            candidates,
            key=lambda x: (
                1 if x.passed_all_gates else 0,
                x.css_score,
                x.dsr_score,
                x.sortino_ratio,
                -len(x.rejection_reasons),
                # Tie breaker: lexicographically inverted string so ascending ID comes first
                [-ord(c) for c in x.strategy_id],
            ),
            reverse=True,
        )

        winner = None
        ranked_entries = []

        for rank_idx, cand in enumerate(sorted_candidates, start=1):
            tier = cand.tier
            # Designate first candidate as winner if Tier 1 and passed
            if rank_idx == 1 and cand.passed_all_gates and cand.tier == "TIER_1":
                tier = "TIER_1_WINNER"
                cand.explanation = f"🏆 TOURNAMENT WINNER (Rank 1): {cand.explanation}"

            ranked_entry = TournamentLeaderboardEntry(
                rank=rank_idx,
                strategy_id=cand.strategy_id,
                strategy_name=cand.strategy_name,
                css_score=cand.css_score,
                dsr_score=cand.dsr_score,
                tier=tier,
                passed_all_gates=cand.passed_all_gates,
                sortino_ratio=cand.sortino_ratio,
                profit_factor=cand.profit_factor,
                max_drawdown_pct=cand.max_drawdown_pct,
                wfe_ratio=cand.wfe_ratio,
                monte_carlo_ruin_prob=cand.monte_carlo_ruin_prob,
                holdout_sharpe=cand.holdout_sharpe,
                holdout_passed=cand.holdout_passed,
                trials_count=cand.trials_count,
                expected_max_sharpe=cand.expected_max_sharpe,
                lineage=cand.lineage,
                rejection_reasons=cand.rejection_reasons,
                explanation=cand.explanation,
            )

            if rank_idx == 1 and ranked_entry.passed_all_gates:
                winner = ranked_entry

            ranked_entries.append(ranked_entry)

        surviving = sum(1 for c in ranked_entries if c.passed_all_gates)
        rejected = len(ranked_entries) - surviving

        trial_stats = {
            "mean_sharpe": float(np.mean(self._trial_sharpes)) if self._trial_sharpes else 0.0,
            "max_sharpe": float(np.max(self._trial_sharpes)) if self._trial_sharpes else 0.0,
            "var_sharpe": float(np.var(self._trial_sharpes, ddof=1)) if len(self._trial_sharpes) > 1 else 0.0,
        }

        return TournamentReport(
            total_trials_evaluated=self._total_trials_evaluated,
            unique_strategies_count=len(self._submitted_hashes),
            surviving_count=surviving,
            rejected_count=rejected,
            winner=winner,
            leaderboard=ranked_entries,
            trial_sharpes_summary=trial_stats,
        )

    def reset_trials(self, confirm_reset: bool = False) -> None:
        """Reset trial counters and submission cache. Requires explicit confirmation."""
        if not confirm_reset:
            raise ValueError(
                "Resetting tournament trials requires explicit confirmation (confirm_reset=True). "
                "Silent trial counter resets are prohibited by anti-overfitting policy."
            )
        self._total_trials_evaluated = 0
        self._submitted_hashes.clear()
        self._evaluated_candidates.clear()
        self._trial_sharpes.clear()
        logger.warning("TournamentManager state and trial counters have been reset.")
