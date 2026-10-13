"""AI Strategy Factory, Agents, and Pine Script Transpiler module."""

from src.ai.pine_transpiler import (
    ParityMetricComparison,
    ParityReport,
    PineParityHarness,
    PineScriptTranspiler,
    PineStaticValidator,
    PineTranspilationError,
    PineTranspilerConfig,
    PineValidationError,
    PineValidationResult,
)
from src.ai.summarizer import (
    ISDiagnosticRecord,
    MarketBrief,
    MarketFeatureSummarizer,
    ParameterPlateauRecord,
    RegimeDistributionProfile,
    SessionVolatilityProfile,
    StrategyResearchContext,
)

from src.ai.tournament import (
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

from src.ai.report_generator import StrategyDossierGenerator
from src.ai.strategy_factory import (
    BatchFactoryResult,
    BatchStrategyFactory,
    StrategyFactoryConfig,
)

__all__ = [
    "PineScriptTranspiler",
    "PineStaticValidator",
    "PineParityHarness",
    "PineTranspilerConfig",
    "PineValidationResult",
    "ParityReport",
    "ParityMetricComparison",
    "PineTranspilationError",
    "PineValidationError",
    "MarketBrief",
    "MarketFeatureSummarizer",
    "StrategyResearchContext",
    "SessionVolatilityProfile",
    "RegimeDistributionProfile",
    "ISDiagnosticRecord",
    "ParameterPlateauRecord",
    "TournamentManager",
    "TournamentLeaderboardEntry",
    "TournamentReport",
    "CandidateLineage",
    "DSRResult",
    "HoldoutValidationGate",
    "HoldoutValidationResult",
    "calculate_dsr",
    "calculate_dsr_from_returns",
    "calculate_expected_max_sharpe",
    "calculate_psr",
    "compute_strategy_hash",
    "partition_train_holdout",
    "BatchStrategyFactory",
    "StrategyFactoryConfig",
    "BatchFactoryResult",
    "StrategyDossierGenerator",
]

