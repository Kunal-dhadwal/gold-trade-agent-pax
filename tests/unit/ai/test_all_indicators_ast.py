"""Tests for comprehensive indicator coverage and session filtering in AST interpreter."""

from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest

from src.strategy.contracts import (
    ASTParameterSpec,
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
from src.strategy.interpreter import ASTStrategy, compute_indicators


@pytest.fixture
def multi_indicator_df():
    n_bars = 100
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="1h", tz=timezone.utc)
    closes = 2000.0 + np.sin(np.linspace(0, 10, n_bars)) * 20.0
    return pd.DataFrame(
        {
            "timestamp": dates,
            "open": closes - 0.5,
            "high": closes + 2.0,
            "low": closes - 2.0,
            "close": closes,
            "volume": np.full(n_bars, 1000.0),
        }
    )


def test_compute_all_indicators(multi_indicator_df):
    specs = [
        IndicatorSpec(alias="sma_20", name=IndicatorNameEnum.SMA, inputs={"period": 20}),
        IndicatorSpec(alias="macd_def", name=IndicatorNameEnum.MACD, inputs={"fast": 12, "slow": 26, "signal": 9}),
        IndicatorSpec(alias="bb_20", name=IndicatorNameEnum.BOLLINGER, inputs={"period": 20, "num_std": 2.0}),
        IndicatorSpec(alias="adx_14", name=IndicatorNameEnum.ADX, inputs={"period": 14}),
        IndicatorSpec(alias="don_20", name=IndicatorNameEnum.DONCHIAN, inputs={"period": 20}),
    ]

    indicators = compute_indicators(multi_indicator_df, specs)

    assert "sma_20" in indicators
    assert "macd_def_line" in indicators
    assert "macd_def_signal" in indicators
    assert "bb_20_upper" in indicators
    assert "bb_20_lower" in indicators
    assert "adx_14" in indicators
    assert "don_20_upper" in indicators
    assert "don_20_lower" in indicators


def test_ast_strategy_with_session_constraints(multi_indicator_df):
    proposal = StrategyProposal(
        name="XAU_LONDON_ONLY",
        hypothesis="Strategy restricted strictly to London session trading.",
        direction=TradeDirection.SHORT,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.RANGING],
        indicators=[
            IndicatorSpec(alias="sma_10", name=IndicatorNameEnum.SMA, inputs={"period": 10}),
        ],
        parameters={
            "sma_period": ASTParameterSpec(type="INT", default=10, min_value=5, max_value=50, step=1)
        },
        entry_rules=ConditionNode(
            operator="AND",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                    comparator="<",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="sma_10", lag=0),
                )
            ],
        ),
        exit_rules=ConditionNode(
            operator="OR",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                    comparator=">",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="sma_10", lag=0),
                )
            ],
        ),
        stop_loss=StopLossSpec(type="FIXED_POINTS", multiplier_or_value=10.0),
        take_profit=TakeProfitSpec(type="FIXED_POINTS", multiplier_or_value=20.0),
        constraints=ExecutionConstraints(max_holding_bars=24, allowed_sessions=["LONDON"]),
        rationale="London-only short mean reversion model.",
    )

    strategy = ASTStrategy(proposal=proposal)
    param_specs = strategy.define_parameters()
    assert len(param_specs) == 1
    assert param_specs[0].name == "sma_period"

    signals = strategy.generate_signals(multi_indicator_df)
    assert isinstance(signals, list)

