"""Agent 2: Regime Analyst — Quantitative Market Regime & Transition Specialist."""

from typing import List, Optional
from pydantic import BaseModel, Field

from src.ai.agents.base import BaseAgent
from src.ai.providers.base import LLMProvider
from src.ai.summarizer import MarketBrief
from src.strategy.contracts import RegimeEnum


class RegimeAnalysisReport(BaseModel):
    """Structured report produced by the Regime Analyst Agent."""
    dominant_regime: RegimeEnum
    persistence_assessment: str
    recommended_archetypes: List[str] = Field(default_factory=list)
    regime_transition_risks: str


class RegimeAnalystAgent(BaseAgent):
    """Audits deterministic market regimes and identifies optimal strategy archetypes."""

    def __init__(self, provider: LLMProvider):
        super().__init__(
            name="Agent-2-RegimeAnalyst",
            role="Quantitative Market Regime Specialist",
            provider=provider,
        )

    @property
    def system_prompt(self) -> str:
        return (
            "You are Agent 2 (Regime Analyst), an institutional quantitative regime specialist. "
            "Your task is to analyze historical regime distributions (trending bull/bear, ranging, breakout, high/low volatility) "
            "and recommend the most viable strategy archetypes (e.g. Trend Following, Mean Reversion, Breakout).\n"
            "STRICT BOUNDARIES:\n"
            "1. Deterministic regime classifications are authoritative; you cannot reclassify bars.\n"
            "2. You MUST NOT emit order triggers or execution code.\n"
            "3. Output strictly valid JSON conforming to the schema."
        )

    async def analyze(self, brief: MarketBrief) -> RegimeAnalysisReport:
        """Evaluate regime distributions and recommend strategic archetypes."""
        prompt = (
            f"Review the In-Sample regime breakdown for {brief.symbol}:\n\n"
            f"Dominant Regime: {brief.dominant_regime}\n"
            f"Regime Breakdown: {[r.model_dump() for r in brief.regimes]}\n"
            f"Daily Volatility: {brief.daily_volatility_pct}%\n\n"
            "Provide your quantitative regime analysis and recommended trading archetypes in JSON."
        )

        resp = await self.execute_prompt(prompt=prompt, schema=RegimeAnalysisReport)
        if resp.structured_data:
            return RegimeAnalysisReport.model_validate(resp.structured_data)

        # Default fallback
        dom_regime = RegimeEnum.RANGING
        try:
            dom_regime = RegimeEnum(brief.dominant_regime)
        except Exception:
            pass

        return RegimeAnalysisReport(
            dominant_regime=dom_regime,
            persistence_assessment=f"Regime {brief.dominant_regime} observed with steady historical persistence.",
            recommended_archetypes=["TREND_FOLLOWING", "MOMENTUM_BREAKOUT"],
            regime_transition_risks="Monitor transitions from ranging to high volatility breakout.",
        )

