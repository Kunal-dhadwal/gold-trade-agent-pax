"""Institutional validation policies and threshold configurations."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ValidationThresholdsConfig:
    """Configurable institutional thresholds for quantitative validation gates.

    AUTHORITY INVARIANT:
    These values represent immutable risk and statistical policy.
    AI agents possess ZERO authority to modify or relax these thresholds.
    """
    MIN_TRADES_GATE: int = 50
    MIN_EXPECTANCY_SPREAD_RATIO: float = 0.15
    MIN_GROSS_PROFIT_FACTOR: float = 1.30
    MAX_DRAWDOWN_GATE: float = 0.15  # 15.0% peak-to-trough
    MAX_CONSECUTIVE_LOSSES: int = 8
    MIN_OOS_RETENTION_RATIO: float = 0.65  # OOS GPF / IS GPF
    MAX_RUIN_PROBABILITY: float = 0.05  # 5.0% probability of 20% DD
    MIN_WFE_RATIO: float = 0.50  # 50.0% Walk-Forward Efficiency
    MIN_STRESS_PROFIT_FACTOR: float = 1.10  # Under +50% friction
    MAX_PARAM_DEGRADATION: float = 0.40  # Max 40% performance drop under ±10% perturbation
    CSS_TIER1_THRESHOLD: float = 80.0  # Phase 3 Prime Candidate
    CSS_TIER2_THRESHOLD: float = 70.0  # Reserve Candidate


# Global default configuration instance
DEFAULT_VALIDATION_CONFIG = ValidationThresholdsConfig()

