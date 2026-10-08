"""Agent 3: Strategy Researcher — Quantitative Strategy Formulation & Logic Designer."""

from typing import Optional
from src.ai.agents.base import BaseAgent
from src.ai.providers.base import LLMProvider
from src.ai.summarizer import StrategyResearchContext
from src.strategy.contracts import StrategyProposal


class StrategyResearcherAgent(BaseAgent):
    """Formulates pre-registered falsifiable trading hypotheses and emits StrategyProposal ASTs.

    OOS QUARANTINE INVARIANT:
    This agent accepts ONLY In-Sample research context (StrategyResearchContext).
    It is physically isolated from Out-of-Sample validation holdout data.
    """

    def __init__(self, provider: LLMProvider):
        super().__init__(
            name="Agent-3-StrategyResearcher",
            role="Strategy Hypothesis & Logic Designer",
            provider=provider,
        )

    @property
    def system_prompt(self) -> str:
        return (
            "You are Agent 3 (Strategy Researcher), an institutional quantitative strategy designer for Gold (XAUUSD).\n"
            "Your task is to propose falsifiable trading hypotheses and express them strictly as a machine-readable "
            "StrategyProposal AST JSON object.\n\n"
            "MANDATORY ARCHITECTURAL CONSTRAINTS:\n"
            "1. You must specify an explicit 'direction' ('LONG' or 'SHORT'). Never infer direction implicitly.\n"
            "2. All indicator and price comparisons must use 'ValueRef' with non-negative lags (0 <= lag <= 20). Negative lags are STRICTLY FORBIDDEN.\n"
            "3. Institutional Risk:Reward gate: take_profit multiplier / stop_loss multiplier MUST be >= 1.50.\n"
            "4. Allowed indicators: EMA, SMA, RSI, ATR, BOLLINGER, MACD, ADX, DONCHIAN.\n"
            "5. NO arbitrary Python code, NO eval/exec expressions, NO external library calls.\n"
            "6. Output must be strictly valid JSON matching the StrategyProposal schema."
        )

    async def propose_strategy(
        self,
        context: StrategyResearchContext,
        feedback: Optional[str] = None,
    ) -> StrategyProposal:
        """Formulate a strategy proposal given quarantined In-Sample research context."""
        prompt = (
            f"Formulate a quantitative trading strategy for Spot Gold (XAUUSD) based on the following In-Sample research context:\n\n"
            f"Thesis Statement: {context.thesis_statement}\n"
            f"Target Market Regime: {context.target_regime.value}\n"
            f"Market Brief: {context.market_brief.model_dump_json(indent=2)}\n\n"
        )
        if context.is_diagnostic_history:
            prompt += f"Prior In-Sample Diagnostics (Learn from these failures): {[d.model_dump() for d in context.is_diagnostic_history]}\n\n"
        if feedback:
            prompt += f"Prior Iteration Feedback: {feedback}\n\n"

        prompt += (
            "Emit a complete, valid StrategyProposal JSON adhering strictly to the Pydantic AST schema. "
            "Ensure explicit direction ('LONG' or 'SHORT'), valid lags (0-20), and minimum 1.5 Risk:Reward."
        )

        resp = await self.execute_prompt(prompt=prompt, schema=StrategyProposal)
        if resp.structured_data:
            return StrategyProposal.model_validate(resp.structured_data)

        raise RuntimeError(
            f"Agent 3 failed to generate a valid StrategyProposal. Raw output: {resp.content[:200]}"
        )

