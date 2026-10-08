"""Unit tests for P2-2: Active target_regimes filtering in ASTStrategy.generate_signals()."""

from datetime import datetime, timezone
import pandas as pd
import pytest

from src.core.constants import MarketRegimeType, OrderDirection
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


def create_base_proposal(
    direction: TradeDirection = TradeDirection.LONG,
    target_regimes: list = None,
) -> StrategyProposal:
    """Helper to build a valid StrategyProposal for regime testing."""
    if target_regimes is None:
        target_regimes = [RegimeEnum.BREAKOUT]

    return StrategyProposal(
        name="XAU_REGIME_TEST",
        hypothesis="Strategy testing active target_regimes filtering on entry bars.",
        direction=direction,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=target_regimes,
        indicators=[
            IndicatorSpec(alias="ema_20", name=IndicatorNameEnum.EMA, inputs={"period": 20})
        ],
        entry_rules=ConditionNode(
            operator="AND",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                    comparator=">",
                    right=ValueRef(type=ValueRefType.CONSTANT, value=1990.0, lag=0),
                )
            ],
        ),
        exit_rules=ConditionNode(
            operator="OR",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                    comparator="<",
                    right=ValueRef(type=ValueRefType.CONSTANT, value=1900.0, lag=0),
                )
            ],
        ),
        stop_loss=StopLossSpec(type="FIXED_POINTS", multiplier_or_value=10.0),
        take_profit=TakeProfitSpec(type="FIXED_POINTS", multiplier_or_value=20.0),
        constraints=ExecutionConstraints(max_holding_bars=10, allowed_sessions=["ALL"]),
        rationale="Regime filter unit tests.",
    )


def build_test_df(regimes: list, closes: list = None) -> pd.DataFrame:
    """Construct deterministic test dataframe with specific regime annotations."""
    n = len(regimes)
    dates = pd.date_range("2024-01-01", periods=n, freq="1h", tz=timezone.utc)
    if closes is None:
        closes = [2000.0] * n
    return pd.DataFrame(
        {
            "timestamp": dates,
            "open": [c - 1.0 for c in closes],
            "high": [c + 2.0 for c in closes],
            "low": [c - 2.0 for c in closes],
            "close": closes,
            "volume": [1000.0] * n,
            "regime": regimes,
        }
    )


# ==============================================================================
# 1. BREAKOUT TARGET + BREAKOUT BAR -> ENTRY ALLOWED
# ==============================================================================
def test_regime_breakout_target_allowed_on_breakout_bar():
    proposal = create_base_proposal(target_regimes=[RegimeEnum.BREAKOUT])
    strategy = ASTStrategy(proposal)

    df = build_test_df(regimes=["BREAKOUT"] * 10)
    signals = strategy.generate_signals(df)

    assert len(signals) > 0
    assert signals[0].regime == MarketRegimeType.BREAKOUT


# ==============================================================================
# 2. BREAKOUT TARGET + RANGING BAR -> ENTRY REJECTED
# ==============================================================================
def test_regime_breakout_target_rejected_on_ranging_bar():
    proposal = create_base_proposal(target_regimes=[RegimeEnum.BREAKOUT])
    strategy = ASTStrategy(proposal)

    df = build_test_df(regimes=["RANGING"] * 10)
    signals = strategy.generate_signals(df)

    assert len(signals) == 0


# ==============================================================================
# 3. MULTIPLE TARGET REGIMES
# ==============================================================================
def test_multiple_target_regimes():
    proposal = create_base_proposal(
        target_regimes=[RegimeEnum.TRENDING_BULL, RegimeEnum.BREAKOUT]
    )
    strategy = ASTStrategy(proposal)

    # Bar 0-4: RANGING (rejected)
    # Bar 5-7: TRENDING_UP (allowed)
    # Bar 8-9: BREAKOUT (allowed)
    regimes = ["RANGING"] * 5 + ["TRENDING_UP"] * 3 + ["BREAKOUT"] * 2
    df = build_test_df(regimes=regimes)
    signals = strategy.generate_signals(df)

    assert len(signals) > 0
    assert signals[0].timestamp == df.iloc[5]["timestamp"]


# ==============================================================================
# 4. REGIME CHANGES BETWEEN BARS
# ==============================================================================
def test_regime_changes_between_bars():
    proposal = create_base_proposal(target_regimes=[RegimeEnum.TRENDING_BULL])
    strategy = ASTStrategy(proposal)

    regimes = ["RANGING", "RANGING", "TRENDING_BULL", "RANGING", "RANGING"]
    df = build_test_df(regimes=regimes)
    signals = strategy.generate_signals(df)

    assert len(signals) == 1
    assert signals[0].timestamp == df.iloc[2]["timestamp"]


# ==============================================================================
# 5. EXIT BEHAVIOR WHILE REGIME CHANGES
# ==============================================================================
def test_exit_behavior_while_regime_changes():
    """An open position must not be prematurely closed just because the regime changes."""
    proposal = create_base_proposal(target_regimes=[RegimeEnum.BREAKOUT])
    # Add an explicit exit rule that triggers on bar 4 (close < 1950)
    proposal.exit_rules = ConditionNode(
        operator="OR",
        conditions=[
            ConditionComparison(
                left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                comparator="<",
                right=ValueRef(type=ValueRefType.CONSTANT, value=1950.0, lag=0),
            )
        ],
    )
    strategy = ASTStrategy(proposal)

    # Bar 0: BREAKOUT (triggers entry)
    # Bars 1-3: RANGING (regime shifted, but position must remain open!)
    # Bar 4: RANGING, close drops to 1940 (exit rule triggers)
    regimes = ["BREAKOUT", "RANGING", "RANGING", "RANGING", "RANGING", "BREAKOUT"]
    closes = [2000.0, 2005.0, 2002.0, 2001.0, 1940.0, 2000.0]
    df = build_test_df(regimes=regimes, closes=closes)

    signals = strategy.generate_signals(df)
    # Initial entry signal on Bar 0
    # Second entry signal on Bar 5 after Bar 4 exited
    assert len(signals) == 2
    assert signals[0].timestamp == df.iloc[0]["timestamp"]
    assert signals[1].timestamp == df.iloc[5]["timestamp"]


# ==============================================================================
# 6. MISSING REGIME COLUMN
# ==============================================================================
def test_missing_regime_column_fallback():
    """When dataframe has no 'regime' column, entry logic falls back gracefully."""
    proposal = create_base_proposal(target_regimes=[RegimeEnum.BREAKOUT])
    strategy = ASTStrategy(proposal)

    dates = pd.date_range("2024-01-01", periods=10, freq="1h", tz=timezone.utc)
    closes = [2000.0] * 10
    df_no_regime = pd.DataFrame(
        {
            "timestamp": dates,
            "open": [1999.0] * 10,
            "high": [2002.0] * 10,
            "low": [1998.0] * 10,
            "close": closes,
            "volume": [1000.0] * 10,
        }
    )

    signals = strategy.generate_signals(df_no_regime)
    assert len(signals) > 0


# ==============================================================================
# 7. INVALID REGIME VALUE
# ==============================================================================
def test_invalid_regime_value_safely_ignored():
    """Corrupted or unknown regime strings safely reject entry without throwing."""
    proposal = create_base_proposal(target_regimes=[RegimeEnum.BREAKOUT])
    strategy = ASTStrategy(proposal)

    df = build_test_df(regimes=["UNKNOWN_CHAOS_REGIME", None, 12345] + ["BREAKOUT"] * 5)
    signals = strategy.generate_signals(df)

    assert len(signals) == 1
    assert signals[0].timestamp == df.iloc[3]["timestamp"]


# ==============================================================================
# 8. LONG STRATEGY DIRECTION
# ==============================================================================
def test_regime_filter_long_strategy():
    proposal = create_base_proposal(
        direction=TradeDirection.LONG,
        target_regimes=[RegimeEnum.TRENDING_BULL],
    )
    strategy = ASTStrategy(proposal)

    df = build_test_df(regimes=["TRENDING_UP"] * 6)
    signals = strategy.generate_signals(df)

    assert len(signals) > 0
    assert signals[0].direction == OrderDirection.LONG
    assert signals[0].regime == MarketRegimeType.TRENDING_UP


# ==============================================================================
# 9. SHORT STRATEGY DIRECTION
# ==============================================================================
def test_regime_filter_short_strategy():
    proposal = create_base_proposal(
        direction=TradeDirection.SHORT,
        target_regimes=[RegimeEnum.TRENDING_BEAR],
    )
    strategy = ASTStrategy(proposal)

    df = build_test_df(regimes=["TRENDING_DOWN"] * 6)
    signals = strategy.generate_signals(df)

    assert len(signals) > 0
    assert signals[0].direction == OrderDirection.SHORT
    assert signals[0].regime == MarketRegimeType.TRENDING_DOWN

