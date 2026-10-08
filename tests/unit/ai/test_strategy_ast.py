"""Unit tests for Strategy AST schemas, Pydantic contracts, and deterministic execution."""

from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from src.core.constants import OrderDirection
from src.backtest.engine import BacktestEngine
from src.backtest.types import BacktestConfig
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
from src.strategy.interpreter import ASTStrategy


@pytest.fixture
def sample_ohlcv_df():
    """Deterministic synthetic OHLCV data for testing."""
    n_bars = 200
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="1h", tz=timezone.utc)
    base_price = 2000.0
    # Create trending followed by ranging series
    trend = np.linspace(0, 50, n_bars)
    noise = np.sin(np.linspace(0, 20, n_bars)) * 5.0
    closes = base_price + trend + noise
    highs = closes + 2.0
    lows = closes - 2.0
    opens = closes - 0.5
    volumes = np.full(n_bars, 1000.0)

    return pd.DataFrame(
        {
            "timestamp": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


def test_valid_long_strategy_proposal():
    """Verify building a valid LONG StrategyProposal."""
    proposal = StrategyProposal(
        name="XAU_TREND_LONG",
        hypothesis="Breakout of prior high with RSI confirmation yields positive expectancy in Gold.",
        direction=TradeDirection.LONG,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.TRENDING_BULL],
        indicators=[
            IndicatorSpec(alias="rsi_14", name=IndicatorNameEnum.RSI, inputs={"period": 14}),
            IndicatorSpec(alias="ema_20", name=IndicatorNameEnum.EMA, inputs={"period": 20}),
        ],
        entry_rules=ConditionNode(
            operator="AND",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                    comparator=">",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_20", lag=0),
                ),
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="rsi_14", lag=1),
                    comparator=">",
                    right=ValueRef(type=ValueRefType.CONSTANT, value=50.0, lag=0),
                ),
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
        stop_loss=StopLossSpec(type="ATR_MULTIPLE", multiplier_or_value=1.5),
        take_profit=TakeProfitSpec(type="ATR_MULTIPLE", multiplier_or_value=3.0),
        constraints=ExecutionConstraints(max_holding_bars=48, allowed_sessions=["ALL"]),
        rationale="Strong trend following mechanics with conservative ATR risk controls.",
    )

    assert proposal.direction == TradeDirection.LONG
    assert proposal.stop_loss.multiplier_or_value == 1.5
    assert proposal.take_profit.multiplier_or_value == 3.0


def test_valid_short_strategy_proposal():
    """Verify building a valid SHORT StrategyProposal."""
    proposal = StrategyProposal(
        name="XAU_TREND_SHORT",
        hypothesis="Breakdown below EMA with low RSI indicates sustained downward momentum.",
        direction=TradeDirection.SHORT,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.TRENDING_BEAR],
        indicators=[
            IndicatorSpec(alias="ema_50", name=IndicatorNameEnum.EMA, inputs={"period": 50}),
        ],
        entry_rules=ConditionNode(
            operator="AND",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                    comparator="<",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_50", lag=0),
                )
            ],
        ),
        exit_rules=ConditionNode(
            operator="OR",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                    comparator=">",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_50", lag=0),
                )
            ],
        ),
        stop_loss=StopLossSpec(type="FIXED_POINTS", multiplier_or_value=10.0),
        take_profit=TakeProfitSpec(type="FIXED_POINTS", multiplier_or_value=20.0),
        rationale="Symmetric short continuation model.",
    )

    assert proposal.direction == TradeDirection.SHORT


def test_rejection_negative_lag():
    """Verify that negative lags (lookahead bias) are strictly rejected by schema."""
    with pytest.raises(ValidationError) as exc_info:
        ValueRef(type=ValueRefType.PRICE, name="close", lag=-1)
    assert "Input should be greater than or equal to 0" in str(exc_info.value)


def test_rejection_excessive_lag():
    """Verify that lag > 20 is strictly rejected."""
    with pytest.raises(ValidationError) as exc_info:
        ValueRef(type=ValueRefType.PRICE, name="close", lag=25)
    assert "Input should be less than or equal to 20" in str(exc_info.value)


def test_rejection_constant_non_zero_lag():
    """Verify that CONSTANT ValueRef cannot have a non-zero lag."""
    with pytest.raises(ValidationError) as exc_info:
        ValueRef(type=ValueRefType.CONSTANT, value=50.0, lag=2)
    assert "Constants cannot have a non-zero lag" in str(exc_info.value)


def test_rejection_invalid_price_field():
    """Verify that non-OHLCV price fields are rejected."""
    with pytest.raises(ValidationError) as exc_info:
        ValueRef(type=ValueRefType.PRICE, name="future_predict")
    assert "Invalid price field" in str(exc_info.value)


def test_rejection_violating_risk_reward_gate():
    """Verify that StrategyProposal with R:R < 1.5 is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        StrategyProposal(
            name="XAU_BAD_RR",
            hypothesis="Unfavorable risk reward strategy that should be rejected by gate.",
            direction=TradeDirection.LONG,
            instrument=InstrumentEnum.XAUUSD,
            timeframe=TimeframeEnum.H1,
            data_source=DataSourceEnum.DUKASCOPY,
            target_regimes=[RegimeEnum.RANGING],
            indicators=[IndicatorSpec(alias="sma_20", name=IndicatorNameEnum.SMA, inputs={"period": 20})],
            entry_rules=ConditionNode(
                operator="AND",
                conditions=[
                    ConditionComparison(
                        left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                        comparator=">",
                        right=ValueRef(type=ValueRefType.INDICATOR, name="sma_20", lag=0),
                    )
                ],
            ),
            exit_rules=ConditionNode(
                operator="OR",
                conditions=[
                    ConditionComparison(
                        left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                        comparator="<",
                        right=ValueRef(type=ValueRefType.INDICATOR, name="sma_20", lag=0),
                    )
                ],
            ),
            stop_loss=StopLossSpec(type="ATR_MULTIPLE", multiplier_or_value=2.0),
            take_profit=TakeProfitSpec(type="ATR_MULTIPLE", multiplier_or_value=2.0),  # R:R = 1.0 < 1.5!
            rationale="Defective R:R proposal.",
        )
    assert "Risk:Reward ratio 1.00 violates institutional minimum of 1.50" in str(exc_info.value)


def test_ast_strategy_execution_and_backtest(sample_ohlcv_df):
    """Verify compiling ASTStrategy from proposal and executing in BacktestEngine."""
    proposal = StrategyProposal(
        name="XAU_TEST_EMA_CROSS",
        hypothesis="Fast EMA crossing slow EMA signals momentum continuation in Gold.",
        direction=TradeDirection.LONG,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.TRENDING_BULL],
        indicators=[
            IndicatorSpec(alias="ema_fast", name=IndicatorNameEnum.EMA, inputs={"period": 10}),
            IndicatorSpec(alias="ema_slow", name=IndicatorNameEnum.EMA, inputs={"period": 30}),
        ],
        entry_rules=ConditionNode(
            operator="AND",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="ema_fast", lag=0),
                    comparator="CROSS_OVER",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_slow", lag=0),
                )
            ],
        ),
        exit_rules=ConditionNode(
            operator="OR",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="ema_fast", lag=0),
                    comparator="CROSS_UNDER",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_slow", lag=0),
                )
            ],
        ),
        stop_loss=StopLossSpec(type="ATR_MULTIPLE", multiplier_or_value=1.5),
        take_profit=TakeProfitSpec(type="ATR_MULTIPLE", multiplier_or_value=3.0),
        constraints=ExecutionConstraints(max_holding_bars=50),
        rationale="Classic trend crossing AST implementation.",
    )

    strategy = ASTStrategy(proposal=proposal)
    signals = strategy.generate_signals(sample_ohlcv_df)

    assert isinstance(signals, list)
    for sig in signals:
        assert sig.direction == OrderDirection.LONG
        assert sig.take_profit > sig.entry_price > sig.stop_loss
        assert sig.risk_reward >= 1.5

    # Run deterministic BacktestEngine
    engine = BacktestEngine(config=BacktestConfig(initial_capital=10000.0))
    result = engine.run(sample_ohlcv_df, strategy, instrument="XAUUSD", timeframe="1h")

    assert result.strategy_id == str(proposal.strategy_id)
    assert result.metrics is not None
    assert result.metrics.initial_capital == 10000.0

