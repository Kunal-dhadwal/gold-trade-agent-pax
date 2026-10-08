"""Deterministic Composite Strategy Score (CSS) calculation and candidate ranking."""

from dataclasses import dataclass
from typing import Any, Dict, Optional
import numpy as np

from src.quant.validation.config import DEFAULT_VALIDATION_CONFIG, ValidationThresholdsConfig


@dataclass
class StrategyScoreResult:
    """Detailed breakdown of Composite Strategy Score components."""
    css_score: float
    tier: str  # "TIER_1", "TIER_2", "DISQUALIFIED"
    s1_sortino: float
    s2_drawdown: float
    s3_gpf: float
    s4_wfe: float
    s5_monte_carlo: float
    s6_degradation: float
    passed_hard_gates: bool
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "css_score": round(self.css_score, 2),
            "tier": self.tier,
            "components": {
                "s1_sortino": round(self.s1_sortino, 4),
                "s2_drawdown": round(self.s2_drawdown, 4),
                "s3_gpf": round(self.s3_gpf, 4),
                "s4_wfe": round(self.s4_wfe, 4),
                "s5_monte_carlo": round(self.s5_monte_carlo, 4),
                "s6_degradation": round(self.s6_degradation, 4),
            },
            "passed_hard_gates": self.passed_hard_gates,
            "summary": self.summary,
        }


class CompositeStrategyScorer:
    """Calculates deterministic Composite Strategy Score adhering strictly to ADR-010."""

    WEIGHTS = {
        "w1": 0.25,  # Sortino
        "w2": 0.20,  # MaxDD
        "w3": 0.15,  # Gross Profit Factor
        "w4": 0.15,  # Walk-Forward Efficiency
        "w5": 0.15,  # Monte Carlo P95 DD
        "w6": 0.10,  # Parameter Degradation
    }

    def __init__(self, config: Optional[ValidationThresholdsConfig] = None):
        self.config = config or DEFAULT_VALIDATION_CONFIG

    def compute_score(
        self,
        sortino_oos: float,
        max_dd_oos: float,
        gpf_oos: float,
        wfe: float,
        p95_dd_mc: float,
        degradation: float,
        passed_hard_gates: bool = True,
    ) -> StrategyScoreResult:
        """Compute CSS score normalized in [0.0, 100.0].

        HARD GATE INVARIANT:
        If passed_hard_gates is False, CSS is strictly set to 0.0 and DISQUALIFIED.
        CSS possesses zero authority to resurrect a rejected strategy.
        """
        if not passed_hard_gates:
            return StrategyScoreResult(
                css_score=0.0,
                tier="DISQUALIFIED",
                s1_sortino=0.0,
                s2_drawdown=0.0,
                s3_gpf=0.0,
                s4_wfe=0.0,
                s5_monte_carlo=0.0,
                s6_degradation=0.0,
                passed_hard_gates=False,
                summary="Strategy disqualified by prior hard rejection gates. CSS set to 0.00.",
            )

        # Normalize and clip components to [0.0, 1.0]
        s1 = float(np.clip(sortino_oos / 3.0, 0.0, 1.0))
        s2 = float(np.clip(1.0 - (max_dd_oos / 0.15), 0.0, 1.0))
        s3 = float(np.clip((gpf_oos - 1.0) / 1.5, 0.0, 1.0))
        s4 = float(np.clip(wfe / 1.0, 0.0, 1.0))
        s5 = float(np.clip(1.0 - (p95_dd_mc / 0.20), 0.0, 1.0))
        s6 = float(np.clip(1.0 - (degradation / 0.40), 0.0, 1.0))

        raw_weighted_sum = (
            self.WEIGHTS["w1"] * s1
            + self.WEIGHTS["w2"] * s2
            + self.WEIGHTS["w3"] * s3
            + self.WEIGHTS["w4"] * s4
            + self.WEIGHTS["w5"] * s5
            + self.WEIGHTS["w6"] * s6
        )

        css_score = round(float(raw_weighted_sum * 100.0), 2)

        if css_score >= self.config.CSS_TIER1_THRESHOLD:
            tier = "TIER_1"
            summary = f"Tier 1 Prime Candidate (CSS {css_score:.2f} >= {self.config.CSS_TIER1_THRESHOLD}). Phase 3 Eligible."
        elif css_score >= self.config.CSS_TIER2_THRESHOLD:
            tier = "TIER_2"
            summary = f"Tier 2 Reserve Candidate (CSS {css_score:.2f} >= {self.config.CSS_TIER2_THRESHOLD})."
        else:
            tier = "DISQUALIFIED"
            summary = f"Disqualified (CSS {css_score:.2f} < {self.config.CSS_TIER2_THRESHOLD})."

        return StrategyScoreResult(
            css_score=css_score,
            tier=tier,
            s1_sortino=s1,
            s2_drawdown=s2,
            s3_gpf=s3,
            s4_wfe=s4,
            s5_monte_carlo=s5,
            s6_degradation=s6,
            passed_hard_gates=True,
            summary=summary,
        )

