"""AI Strategy Tournament interface and re-exports for research agents."""

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
    compute_strategy_hash,
    partition_train_holdout,
)

__all__ = [
    "CandidateLineage",
    "DSRResult",
    "HoldoutValidationGate",
    "HoldoutValidationResult",
    "TournamentLeaderboardEntry",
    "TournamentManager",
    "TournamentReport",
    "calculate_dsr",
    "calculate_dsr_from_returns",
    "calculate_expected_max_sharpe",
    "calculate_psr",
    "compute_strategy_hash",
    "partition_train_holdout",
]

