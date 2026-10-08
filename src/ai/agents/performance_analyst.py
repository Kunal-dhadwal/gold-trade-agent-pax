"""Agent 5: Performance Analyst — Research Teardown & Synthesis Specialist."""

from typing import List, Optional
from pydantic import BaseModel, Field

from src.ai.agents.base import BaseAgent
from src.ai.providers.base import LLMProvider
from src.quant.validation.pipeline import ValidationPipelineReport


class PerformanceEvaluationReport(BaseModel):
    """Evaluation summary and sanitized feedback for iterative strategy refinement."""
    strategy_name: str
    overall_status: str
    strengths: List[str] = Field(default_factory=list)
    vulnerabilities: List[str] = Field(default_factory=list)
    sanitized_feedback_for_researcher: str


class PerformanceAnalystAgent(BaseAgent):
    """Analyzes validation outcomes and produces sanitized research feedback."""

    def __init__(self, provider: LLMProvider):
        super().__init__(
            name="Agent-5-PerformanceAnalyst",
            role="Portfolio Optimization & Research Teardown Specialist",
            provider=provider,
        )

    @property
    def system_prompt(self) -> str:
        return (
            "You are Agent 5 (Performance Analyst), an institutional portfolio evaluation specialist.\n"
            "Your task is to analyze deterministic validation results, synthesize strengths and weaknesses, "
            "and formulate actionable feedback for future strategy iterations.\n\n"
            "CRITICAL OOS QUARANTINE INVARIANT:\n"
            "Your 'sanitized_feedback_for_researcher' must be qualitative and conceptual (e.g. 'Improve entry confirmation in ranging regimes'). "
            "You MUST NEVER reveal exact Out-of-Sample holdout dates, specific OOS trade logs, or forward equity values to the Strategy Researcher."
        )

    async def evaluate(self, report: ValidationPipelineReport) -> PerformanceEvaluationReport:
        """Synthesize validation report into an actionable evaluation report."""
        prompt = (
            f"Review the quantitative validation results for strategy '{report.strategy_name}':\n\n"
            f"Overall Passed: {report.overall_passed}\n"
            f"Final Tier: {report.final_tier}\n"
            f"Stage Details: {[s.stage_name + ': ' + ('PASS' if s.passed else 'FAIL (' + str(s.rejection_reason) + ')') for s in report.stages]}\n\n"
            "Provide your institutional evaluation and sanitized qualitative guidance in JSON format."
        )

        resp = await self.execute_prompt(prompt=prompt, schema=PerformanceEvaluationReport)
        if resp.structured_data:
            return PerformanceEvaluationReport.model_validate(resp.structured_data)

        # Fallback evaluation
        status = "PASSED" if report.overall_passed else "REJECTED"
        return PerformanceEvaluationReport(
            strategy_name=report.strategy_name,
            overall_status=status,
            strengths=["Validated via cascading quantitative pipeline."],
            vulnerabilities=["Failed validation gates" if not report.overall_passed else "None identified."],
            sanitized_feedback_for_researcher=(
                "Focus on refining entry threshold sensitivity and minimizing false signals in transitional regimes."
                if not report.overall_passed
                else "Candidate meets quantitative robustness standards."
            ),
        )

