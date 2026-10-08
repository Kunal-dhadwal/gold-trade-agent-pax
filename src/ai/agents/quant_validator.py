"""Agent 4: Quant Validator — Skeptical Quantitative Verification Officer."""

from typing import Optional
import pandas as pd
from src.ai.agents.base import BaseAgent
from src.ai.providers.base import LLMProvider
from src.quant.validation.pipeline import CascadingValidationPipeline, ValidationPipelineReport
from src.strategy.contracts import StrategyProposal


class QuantValidatorAgent(BaseAgent):
    """Executes deterministic cascading early-exit validation on strategy proposals."""

    def __init__(
        self,
        provider: LLMProvider,
        pipeline: Optional[CascadingValidationPipeline] = None,
    ):
        super().__init__(
            name="Agent-4-QuantValidator",
            role="Skeptical Quantitative Verification Officer",
            provider=provider,
        )
        self.pipeline = pipeline or CascadingValidationPipeline()

    @property
    def system_prompt(self) -> str:
        return (
            "You are Agent 4 (Quant Validator), an institutional quantitative risk officer.\n"
            "Your non-negotiable duty is to challenge and deterministically verify every proposed strategy.\n"
            "You do not accept qualitative arguments or backtest claims from other agents. "
            "You evaluate strictly through deterministic Python execution of cascading validation gates."
        )

    def validate(
        self,
        proposal: StrategyProposal,
        df: pd.DataFrame,
        is_df: Optional[pd.DataFrame] = None,
    ) -> ValidationPipelineReport:
        """Execute deterministic cascading validation pipeline.

        DETERMINISTIC VERIFICATION INVARIANT:
        This method executes pure Python/NumPy logic. The LLM is NOT consulted to judge metrics.
        """
        return self.pipeline.run_pipeline(proposal=proposal, df=df, is_df=is_df)
