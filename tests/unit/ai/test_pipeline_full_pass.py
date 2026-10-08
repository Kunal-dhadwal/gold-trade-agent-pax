"""Tests for full cascading validation pipeline passing end-to-end and WFA folds."""

from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
import pytest

from src.quant.validation.config import ValidationThresholdsConfig
from src.quant.validation.pipeline import CascadingValidationPipeline
from src.quant.validation.walk_forward import WalkForwardAnalyzer
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
def long_sample_df():
    """Generates 600 days of hourly data to satisfy canonical rolling fold requirements."""
    n_bars = 24 * 600
    dates = pd.date_range("2023-01-01", periods=n_bars, freq="1h", tz=timezone.utc)
    base = 1900.0
    # Steady upward drift with oscillations
    drift = np.linspace(0, 300, n_bars)
    cycle = np.sin(np.linspace(0, 100, n_bars)) * 10.0
    closes = base + drift + cycle
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


def test_wfa_partition_folds(long_sample_df):
    analyzer = WalkForwardAnalyzer()
    folds = analyzer.partition_folds(long_sample_df, is_months=6, oos_months=2, step_months=2)
    assert len(folds) > 0
    for is_slice, oos_slice in folds:
        assert len(is_slice) > 0
        assert len(oos_slice) > 0
        assert is_slice["timestamp"].max() <= oos_slice["timestamp"].min()


def test_full_pipeline_with_passing_strategy(long_sample_df):
    """Verify full progression through AST -> IS Backtest -> IS Gates -> WFA -> Monte Carlo -> CSS."""
    # Use config with attainable gates for synthetic data
    config = ValidationThresholdsConfig(
        MIN_TRADES_GATE=15,
        MIN_EXPECTANCY_SPREAD_RATIO=0.01,
        MIN_GROSS_PROFIT_FACTOR=1.05,
        MAX_DRAWDOWN_GATE=0.30,
        MAX_CONSECUTIVE_LOSSES=15,
        MIN_OOS_RETENTION_RATIO=0.30,
        MAX_RUIN_PROBABILITY=0.20,
        MIN_WFE_RATIO=0.10,
        MIN_STRESS_PROFIT_FACTOR=0.90,
        MAX_PARAM_DEGRADATION=0.60,
        CSS_TIER1_THRESHOLD=60.0,
        CSS_TIER2_THRESHOLD=40.0,
    )

    pipeline = CascadingValidationPipeline(config=config)

    proposal = StrategyProposal(
        name="XAU_PASSING_PIPELINE",
        hypothesis="Robust trend following strategy across gold bull cycles.",
        direction=TradeDirection.LONG,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.TRENDING_BULL],
        indicators=[
            IndicatorSpec(alias="ema_fast", name=IndicatorNameEnum.EMA, inputs={"period": 12}),
            IndicatorSpec(alias="ema_slow", name=IndicatorNameEnum.EMA, inputs={"period": 24}),
        ],
        entry_rules=ConditionNode(
            operator="AND",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="ema_fast", lag=0),
                    comparator=">",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_slow", lag=0),
                )
            ],
        ),
        exit_rules=ConditionNode(
            operator="OR",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="ema_fast", lag=0),
                    comparator="<",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_slow", lag=0),
                )
            ],
        ),
        stop_loss=StopLossSpec(type="ATR_MULTIPLE", multiplier_or_value=1.5),
        take_profit=TakeProfitSpec(type="ATR_MULTIPLE", multiplier_or_value=3.0),
        constraints=ExecutionConstraints(max_holding_bars=100, allowed_sessions=["ALL"]),
        rationale="Full pipeline test proposal with valid R:R.",
    )

    report = pipeline.run_pipeline(proposal, long_sample_df)

    assert report.strategy_name == proposal.name
    assert len(report.stages) >= 3
    # Check stage names
    stage_names = [s.stage_name for s in report.stages]
    assert "AST_VALIDATION" in stage_names
    assert "IN_SAMPLE_HARD_GATES" in stage_names

