"""Integration test for full multi-agent collective orchestration (Agents 1-5)."""

from datetime import datetime, timezone
import json
import numpy as np
import pandas as pd
import pytest

from src.ai.agents import (
    MarketAnalystAgent,
    RegimeAnalystAgent,
    StrategyResearcherAgent,
    QuantValidatorAgent,
    PerformanceAnalystAgent,
)
from src.ai.providers import MockLLMProvider
from src.ai.summarizer import MarketFeatureSummarizer, StrategyResearchContext
from src.strategy.contracts import RegimeEnum, StrategyProposal


@pytest.fixture
def sample_historical_df():
    """Deterministic synthetic Gold dataset."""
    n_bars = 250
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="1h", tz=timezone.utc)
    base = 2000.0
    trend = np.linspace(0, 50, n_bars)
    noise = np.sin(np.linspace(0, 25, n_bars)) * 4.0
    closes = base + trend + noise
    return pd.DataFrame(
        {
            "timestamp": dates,
            "open": closes - 0.5,
            "high": closes + 2.0,
            "low": closes - 2.0,
            "close": closes,
            "volume": np.full(n_bars, 800.0),
        }
    )


@pytest.mark.asyncio
async def test_full_multi_agent_workflow(sample_historical_df):
    """Verify end-to-end execution across all 5 specialized agents."""

    # 1. Deterministic Market Feature Summarizer
    summarizer = MarketFeatureSummarizer()
    market_brief = summarizer.summarize(sample_historical_df, symbol="XAUUSD", timeframe="1h")

    # 2. Agent 1: Market Analyst
    market_analyst_provider = MockLLMProvider()
    agent_1 = MarketAnalystAgent(provider=market_analyst_provider)
    market_report = await agent_1.analyze(brief=market_brief)

    assert market_report.market_overview is not None
    assert len(market_report.key_observations) > 0

    # 3. Agent 2: Regime Analyst
    regime_analyst_provider = MockLLMProvider()
    agent_2 = RegimeAnalystAgent(provider=regime_analyst_provider)
    regime_report = await agent_2.analyze(brief=market_brief)

    assert regime_report.dominant_regime is not None
    assert len(regime_report.recommended_archetypes) > 0

    # 4. Agent 3: Strategy Researcher (Quarantined Context)
    research_context = StrategyResearchContext(
        thesis_statement="Gold momentum during London-NY overlap with trend filter provides statistical edge.",
        target_regime=regime_report.dominant_regime,
        market_brief=market_brief,
    )

    canned_proposal_json = json.dumps(
        {
            "name": "XAU_ORCHESTRATED_TREND",
            "hypothesis": "Gold momentum breakout confirmed by EMA cross yields positive expectancy.",
            "direction": "LONG",
            "instrument": "XAUUSD",
            "timeframe": "1h",
            "data_source": "DUKASCOPY",
            "target_regimes": ["TRENDING_BULL"],
            "indicators": [
                {"alias": "ema_12", "name": "EMA", "inputs": {"period": 12}},
                {"alias": "ema_26", "name": "EMA", "inputs": {"period": 26}},
            ],
            "entry_rules": {
                "operator": "AND",
                "conditions": [
                    {
                        "left": {"type": "INDICATOR", "name": "ema_12", "lag": 0},
                        "comparator": "CROSS_OVER",
                        "right": {"type": "INDICATOR", "name": "ema_26", "lag": 0},
                    }
                ],
            },
            "exit_rules": {
                "operator": "OR",
                "conditions": [
                    {
                        "left": {"type": "INDICATOR", "name": "ema_12", "lag": 0},
                        "comparator": "CROSS_UNDER",
                        "right": {"type": "INDICATOR", "name": "ema_26", "lag": 0},
                    }
                ],
            },
            "stop_loss": {"type": "ATR_MULTIPLE", "multiplier_or_value": 1.5},
            "take_profit": {"type": "ATR_MULTIPLE", "multiplier_or_value": 3.0},
            "constraints": {"max_holding_bars": 72, "allowed_sessions": ["ALL"]},
            "rationale": "Orchestration test strategy proposal.",
        }
    )

    researcher_provider = MockLLMProvider(canned_responses=[canned_proposal_json])
    agent_3 = StrategyResearcherAgent(provider=researcher_provider)
    proposal = await agent_3.propose_strategy(context=research_context)

    assert isinstance(proposal, StrategyProposal)
    assert proposal.name == "XAU_ORCHESTRATED_TREND"
    assert proposal.direction.value == "LONG"

    # 5. Agent 4: Quant Validator (Deterministic Verification)
    validator_provider = MockLLMProvider()
    agent_4 = QuantValidatorAgent(provider=validator_provider)
    validation_report = agent_4.validate(proposal=proposal, df=sample_historical_df)

    assert validation_report.strategy_id == str(proposal.strategy_id)
    assert len(validation_report.stages) > 0

    # 6. Agent 5: Performance Analyst (Teardown & Sanitized Feedback)
    perf_provider = MockLLMProvider()
    agent_5 = PerformanceAnalystAgent(provider=perf_provider)
    eval_report = await agent_5.evaluate(report=validation_report)

    assert eval_report.strategy_name == proposal.name
    assert eval_report.sanitized_feedback_for_researcher is not None

