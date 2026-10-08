"""Unit tests for P3-2: Deep AST conjunction integration (3+ atomic conditions)."""

from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest

from src.core.constants import OrderDirection
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


def test_deep_ast_three_condition_conjunction():
    """Verify runtime execution of 3-condition conjunction: RSI < 30 AND EMA_12 > EMA_26 AND ADX > 25."""
    n_bars = 100
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="1h", tz=timezone.utc)
    # Create trending dataset with rising closes
    base = 2000.0
    closes = base + np.linspace(0, 50, n_bars)

    df = pd.DataFrame(
        {
            "timestamp": dates,
            "open": closes - 0.5,
            "high": closes + 2.0,
            "low": closes - 2.0,
            "close": closes,
            "volume": np.full(n_bars, 1000.0),
        }
    )

    proposal = StrategyProposal(
        name="XAU_DEEP_CONJUNCTION",
        hypothesis="Strategy testing 3-condition deep conjunction evaluation in ASTStrategy.",
        direction=TradeDirection.LONG,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.TRENDING_BULL],
        indicators=[
            IndicatorSpec(alias="rsi_14", name=IndicatorNameEnum.RSI, inputs={"period": 14}),
            IndicatorSpec(alias="ema_12", name=IndicatorNameEnum.EMA, inputs={"period": 12}),
            IndicatorSpec(alias="ema_26", name=IndicatorNameEnum.EMA, inputs={"period": 26}),
            IndicatorSpec(alias="adx_14", name=IndicatorNameEnum.ADX, inputs={"period": 14}),
        ],
        entry_rules=ConditionNode(
            operator="AND",
            conditions=[
                # Condition 1: RSI < 90
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="rsi_14", lag=0),
                    comparator="<",
                    right=ValueRef(type=ValueRefType.CONSTANT, value=90.0, lag=0),
                ),
                # Condition 2: EMA_12 > EMA_26
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="ema_12", lag=0),
                    comparator=">",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_26", lag=0),
                ),
                # Condition 3: ADX_14 > 15
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="adx_14", lag=0),
                    comparator=">",
                    right=ValueRef(type=ValueRefType.CONSTANT, value=15.0, lag=0),
                ),
            ],
        ),
        exit_rules=ConditionNode(
            operator="OR",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="ema_12", lag=0),
                    comparator="<",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_26", lag=0),
                )
            ],
        ),
        stop_loss=StopLossSpec(type="ATR_MULTIPLE", multiplier_or_value=1.5),
        take_profit=TakeProfitSpec(type="ATR_MULTIPLE", multiplier_or_value=3.0),
        constraints=ExecutionConstraints(max_holding_bars=40, allowed_sessions=["ALL"]),
        rationale="P3-2 Deep conjunction test.",
    )

    strategy = ASTStrategy(proposal=proposal)
    signals = strategy.generate_signals(df)

    # In a rising trend, EMA_12 > EMA_26, RSI is below 90, and ADX rises above 15
    assert len(signals) > 0
    first_sig = signals[0]
    assert first_sig.direction == OrderDirection.LONG
    assert first_sig.entry_price > 2000.0
    assert first_sig.take_profit > first_sig.entry_price
    assert first_sig.stop_loss < first_sig.entry_price

