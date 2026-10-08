"""Agent 1: Market Analyst — Quantitative Gold Market Structure Specialist."""

import json
from typing import List, Optional
from pydantic import BaseModel, Field

from src.ai.agents.base import BaseAgent
from src.ai.providers.base import LLMProvider
from src.ai.summarizer import MarketBrief


class MarketAnalysisReport(BaseModel):
    """Structured report produced by the Market Analyst Agent."""
    market_overview: str
    dominant_session_dynamics: str
    volatility_characteristics: str
    key_observations: List[str] = Field(default_factory=list)


class MarketAnalystAgent(BaseAgent):
    """Analyzes In-Sample Gold market structure across London, New York, and Asian sessions."""

    def __init__(self, provider: LLMProvider):
        super().__init__(
            name="Agent-1-MarketAnalyst",
            role="Quantitative Gold Market Structure Specialist",
            provider=provider,
        )

    @property
    def system_prompt(self) -> str:
        return (
            "You are Agent 1 (Market Analyst), an institutional quantitative market structure researcher "
            "specializing in Spot Gold (XAUUSD). Your task is to analyze aggregated historical market summaries "
            "and identify session volume tendencies, daily range characteristics, and volatility clustering.\n"
            "STRICT BOUNDARIES:\n"
            "1. You formulate structural market observations ONLY.\n"
            "2. You MUST NOT propose trading signals, entry/exit orders, or live execution logic.\n"
            "3. Your output must strictly conform to the requested JSON schema."
        )

    async def analyze(self, brief: MarketBrief) -> MarketAnalysisReport:
        """Perform quantitative market structure analysis on an In-Sample MarketBrief."""
        prompt = (
            f"Analyze the following In-Sample Gold Market Structure Brief:\n\n"
            f"{brief.model_dump_json(indent=2)}\n\n"
            "Provide your institutional quantitative evaluation in JSON format matching the schema."
        )

        resp = await self.execute_prompt(prompt=prompt, schema=MarketAnalysisReport)
        if resp.structured_data:
            return MarketAnalysisReport.model_validate(resp.structured_data)

        # Fallback if raw text returned
        return MarketAnalysisReport(
            market_overview="Historical Gold market structure evaluated.",
            dominant_session_dynamics=f"Dominant session observed: {brief.dominant_regime}",
            volatility_characteristics=f"Daily volatility measured at {brief.daily_volatility_pct}%.",
            key_observations=["London and New York sessions represent primary liquidity windows."],
        )

