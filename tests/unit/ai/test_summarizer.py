"""Unit and adversarial tests for MarketFeatureSummarizer and OOS Research Quarantine."""

from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest

from src.ai.summarizer import (
    MarketBrief,
    MarketFeatureSummarizer,
    StrategyResearchContext,
)
from src.strategy.contracts import RegimeEnum


@pytest.fixture
def sample_gold_bars():
    n_bars = 100
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="1h", tz=timezone.utc)
    closes = np.linspace(2000.0, 2050.0, n_bars)
    return pd.DataFrame(
        {
            "timestamp": dates,
            "open": closes - 0.5,
            "high": closes + 2.0,
            "low": closes - 2.0,
            "close": closes,
            "volume": np.full(n_bars, 500.0),
        }
    )


def test_market_feature_summarizer_generates_brief(sample_gold_bars):
    summarizer = MarketFeatureSummarizer()
    brief = summarizer.summarize(sample_gold_bars, symbol="XAUUSD", timeframe="1h")

    assert isinstance(brief, MarketBrief)
    assert brief.symbol == "XAUUSD"
    assert brief.total_bars == 100
    assert len(brief.sessions) > 0
    assert len(brief.regimes) > 0
    assert brief.mean_price > 2000.0


def test_strategy_research_context_valid(sample_gold_bars):
    summarizer = MarketFeatureSummarizer()
    brief = summarizer.summarize(sample_gold_bars)

    ctx = StrategyResearchContext(
        thesis_statement="Gold momentum during London open provides statistical edge.",
        target_regime=RegimeEnum.TRENDING_BULL,
        market_brief=brief,
    )

    assert ctx.thesis_statement is not None
    assert ctx.target_regime == RegimeEnum.TRENDING_BULL
    assert ctx.market_brief.total_bars == 100


def test_adversarial_oos_quarantine_rejection(sample_gold_bars):
    """Adversarial test: context builder must violently reject any OOS metrics."""
    summarizer = MarketFeatureSummarizer()
    brief = summarizer.summarize(sample_gold_bars)

    # Attempt to inject OOS data into StrategyResearchContext
    adversarial_payload = {
        "thesis_statement": "Attempting to inject OOS metrics",
        "target_regime": RegimeEnum.TRENDING_BULL,
        "market_brief": brief.model_dump(),
        "oos_metrics": {"sharpe": 2.5, "gpf": 2.1},
    }

    with pytest.raises(ValueError) as exc_info:
        StrategyResearchContext.model_validate(adversarial_payload)

    assert "CRITICAL SECURITY VIOLATION: OOS Quarantine breached!" in str(exc_info.value)
    assert "oos_metrics" in str(exc_info.value)

