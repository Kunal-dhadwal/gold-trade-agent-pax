"""Adversarial security tests for Phase 2 contracts, AST parser, and OOS quarantine."""

import pytest
from pydantic import ValidationError

from src.ai.summarizer import MarketBrief, StrategyResearchContext
from src.strategy.contracts import (
    ConditionComparison,
    ConditionNode,
    DataSourceEnum,
    ExecutionConstraints,
    IndicatorNameEnum,
    IndicatorSpec,
    InstrumentEnum,
    RegimeEnum,
    StopLossSpec,
    StrategyProposal,
    TakeProfitSpec,
    TimeframeEnum,
    TradeDirection,
    ValueRef,
    ValueRefType,
)


def test_adversarial_negative_lag_injection():
    """Adversarial attempt: Injecting negative lag to gain future lookahead access."""
    with pytest.raises(ValidationError) as exc:
        ValueRef(type=ValueRefType.PRICE, name="close", lag=-5)
    assert "greater than or equal to 0" in str(exc.value)


def test_adversarial_excessive_lag_injection():
    """Adversarial attempt: Injecting unbounded lag (>20) to exhaust memory."""
    with pytest.raises(ValidationError) as exc:
        ValueRef(type=ValueRefType.PRICE, name="close", lag=100)
    assert "less than or equal to 20" in str(exc.value)


def test_adversarial_arbitrary_python_code_injection():
    """Adversarial attempt: Injecting Python code into indicator alias or name."""
    with pytest.raises(ValidationError):
        IndicatorSpec(
            alias="__import__('os').system('rm -rf /')",
            name=IndicatorNameEnum.EMA,
            inputs={"period": 20},
        )


def test_adversarial_unauthorized_indicator():
    """Adversarial attempt: Proposing unverified/unregistered indicator."""
    with pytest.raises(ValidationError):
        IndicatorSpec(
            alias="neural_net_alpha",
            name="UNSUPERVISED_LSTM",  # Not in IndicatorNameEnum
            inputs={},
        )


def test_adversarial_oos_leakage_into_research_context():
    """Adversarial attempt: Smuggling OOS equity and Sharpe into StrategyResearcher."""
    fake_brief = MarketBrief(
        symbol="XAUUSD",
        timeframe="1h",
        start_date="2024-01-01T00:00:00Z",
        end_date="2024-06-01T00:00:00Z",
        total_bars=1000,
        mean_price=2050.0,
        price_min=2000.0,
        price_max=2100.0,
        daily_volatility_pct=1.2,
        sessions=[],
        regimes=[],
        dominant_regime="TRENDING_BULL",
        generated_at="2024-06-01T00:00:00Z",
    )

    contaminated_payload = {
        "thesis_statement": "Contaminated prompt attempting data leakage",
        "target_regime": RegimeEnum.TRENDING_BULL,
        "market_brief": fake_brief.model_dump(),
        "oos_sharpe": 2.85,  # Forbidden OOS key!
    }

    with pytest.raises(ValueError) as exc:
        StrategyResearchContext.model_validate(contaminated_payload)
    assert "CRITICAL SECURITY VIOLATION: OOS Quarantine breached!" in str(exc.value)


def test_adversarial_inverted_sl_tp():
    """Adversarial attempt: Setting Take-Profit below Stop-Loss to manipulate R:R."""
    with pytest.raises(ValidationError) as exc:
        StrategyProposal(
            name="XAU_INVERTED_RR",
            hypothesis="Adversarial inverted risk reward proposal.",
            direction=TradeDirection.LONG,
            instrument=InstrumentEnum.XAUUSD,
            timeframe=TimeframeEnum.H1,
            data_source=DataSourceEnum.DUKASCOPY,
            target_regimes=[RegimeEnum.RANGING],
            indicators=[IndicatorSpec(alias="ema_20", name=IndicatorNameEnum.EMA, inputs={"period": 20})],
            entry_rules=ConditionNode(
                operator="AND",
                conditions=[
                    ConditionComparison(
                        left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                        comparator=">",
                        right=ValueRef(type=ValueRefType.INDICATOR, name="ema_20", lag=0),
                    )
                ],
            ),
            exit_rules=ConditionNode(
                operator="OR",
                conditions=[
                    ConditionComparison(
                        left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                        comparator="<",
                        right=ValueRef(type=ValueRefType.INDICATOR, name="ema_20", lag=0),
                    )
                ],
            ),
            stop_loss=StopLossSpec(type="ATR_MULTIPLE", multiplier_or_value=3.0),
            take_profit=TakeProfitSpec(type="ATR_MULTIPLE", multiplier_or_value=1.0),  # R:R = 0.33 < 1.5!
            rationale="Adversarial check with sufficient length to test R:R gate.",
        )
    assert "violates institutional minimum of 1.50" in str(exc.value)
