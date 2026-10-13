"""Unit and regression tests for Pine Script v6 Transpiler, Static Validator, and Parity Harness."""

import math
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest

from src.ai.pine_transpiler import (
    ParityReport,
    PineParityHarness,
    PineScriptTranspiler,
    PineStaticValidator,
    PineTranspilationError,
    PineTranspilerConfig,
    PineValidationError,
    PineValidationResult,
)
from src.backtest.types import BacktestConfig
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


@pytest.fixture
def sample_gold_bars():
    """Deterministic synthetic 1h OHLCV data for Gold testing."""
    n_bars = 250
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="1h", tz=timezone.utc)
    base_price = 2000.0
    trend = np.linspace(0, 40, n_bars)
    noise = np.sin(np.linspace(0, 20, n_bars)) * 6.0
    closes = base_price + trend + noise
    highs = closes + 2.5
    lows = closes - 2.5
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


@pytest.fixture
def dual_ema_proposal():
    """Dual EMA trend following long proposal."""
    return StrategyProposal(
        name="XAU_TEST_DUAL_EMA",
        hypothesis="Fast EMA crossing slow EMA signals persistent directional momentum in Gold.",
        direction=TradeDirection.LONG,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.TRENDING_BULL],
        indicators=[
            IndicatorSpec(alias="ema_fast", name=IndicatorNameEnum.EMA, inputs={"period": 10}),
            IndicatorSpec(alias="ema_slow", name=IndicatorNameEnum.EMA, inputs={"period": 30}),
        ],
        parameters={
            "fast_period": ASTParameterSpec(type="INT", default=10, min_value=5, max_value=50, step=1),
            "slow_period": ASTParameterSpec(type="INT", default=30, min_value=20, max_value=100, step=1),
        },
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
        constraints=ExecutionConstraints(max_holding_bars=48, allowed_sessions=["ALL"]),
        rationale="Institutional trend following cross with volatility bracket stops.",
    )


@pytest.fixture
def atr_breakout_proposal():
    """ATR Donchian channel breakout proposal."""
    return StrategyProposal(
        name="XAU_TEST_ATR_BREAKOUT",
        hypothesis="Expansion above Donchian channel captures sustained volatility breakouts in Gold.",
        direction=TradeDirection.LONG,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.BREAKOUT],
        indicators=[
            IndicatorSpec(alias="donchian_20", name=IndicatorNameEnum.DONCHIAN, inputs={"period": 20}),
            IndicatorSpec(alias="atr_14", name=IndicatorNameEnum.ATR, inputs={"period": 14}),
        ],
        parameters={
            "donchian_period": ASTParameterSpec(type="INT", default=20, min_value=10, max_value=50, step=1),
        },
        entry_rules=ConditionNode(
            operator="AND",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                    comparator=">",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="donchian_20_upper", lag=1),
                )
            ],
        ),
        exit_rules=ConditionNode(
            operator="OR",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                    comparator="<",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="donchian_20_mid", lag=0),
                )
            ],
        ),
        stop_loss=StopLossSpec(type="ATR_MULTIPLE", multiplier_or_value=2.0),
        take_profit=TakeProfitSpec(type="RISK_REWARD_RATIO", multiplier_or_value=2.5),
        constraints=ExecutionConstraints(max_holding_bars=72, allowed_sessions=["ALL"]),
        rationale="Donchian breakout model with trailing risk-reward target.",
    )


@pytest.fixture
def rsi_reversion_proposal():
    """RSI mean reversion with Bollinger Bands proposal."""
    return StrategyProposal(
        name="XAU_TEST_RSI_REVERSION",
        hypothesis="Oversold RSI touching lower Bollinger Band indicates exhaustion in Gold.",
        direction=TradeDirection.LONG,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.RANGING],
        indicators=[
            IndicatorSpec(alias="rsi_14", name=IndicatorNameEnum.RSI, inputs={"period": 14}),
            IndicatorSpec(alias="bb_20", name=IndicatorNameEnum.BOLLINGER, inputs={"period": 20, "num_std": 2.0}),
        ],
        parameters={
            "rsi_oversold": ASTParameterSpec(type="FLOAT", default=30.0, min_value=15.0, max_value=40.0, step=1.0),
        },
        entry_rules=ConditionNode(
            operator="AND",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="rsi_14", lag=0),
                    comparator="<=",
                    right=ValueRef(type=ValueRefType.CONSTANT, value=30.0, lag=0),
                ),
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="low", lag=0),
                    comparator="<=",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="bb_20_lower", lag=0),
                ),
            ],
        ),
        exit_rules=ConditionNode(
            operator="OR",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                    comparator=">=",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="bb_20_middle", lag=0),
                )
            ],
        ),
        stop_loss=StopLossSpec(type="ATR_MULTIPLE", multiplier_or_value=1.5),
        take_profit=TakeProfitSpec(type="RISK_REWARD_RATIO", multiplier_or_value=2.0),
        constraints=ExecutionConstraints(max_holding_bars=36, allowed_sessions=["LONDON", "NEW_YORK"]),
        rationale="Statistical mean reversion with session gating.",
    )


# ==============================================================================
# 1. TRANSPILATION OF CORE STRATEGY PATTERNS
# ==============================================================================

def test_transpile_dual_ema_crossover(dual_ema_proposal):
    """Verify clean Pine Script v6 transpilation of Dual EMA crossover strategy."""
    transpiler = PineScriptTranspiler()
    code = transpiler.transpile(dual_ema_proposal)

    assert "//@version=6" in code
    assert 'strategy(title="XAU_TEST_DUAL_EMA"' in code
    assert "ta.crossover(ema_fast, ema_slow)" in code
    assert "ta.crossunder(ema_fast, ema_slow)" in code
    assert "p_fast_period = input.int(10" in code
    assert "p_slow_period = input.int(30" in code
    assert "strategy.entry(" in code
    assert "strategy.exit(" in code


def test_transpile_atr_breakout(atr_breakout_proposal):
    """Verify transpilation of ATR / Donchian breakout strategy."""
    transpiler = PineScriptTranspiler()
    code = transpiler.transpile(atr_breakout_proposal)

    assert "//@version=6" in code
    assert "donchian_20_upper = ta.highest(high, p_donchian_period)" in code
    assert "donchian_20_lower = ta.lowest(low, p_donchian_period)" in code
    assert "(close > donchian_20_upper[1])" in code
    assert "(close < donchian_20_mid)" in code
    assert "strategy.entry(" in code


def test_transpile_rsi_mean_reversion(rsi_reversion_proposal):
    """Verify transpilation of RSI mean reversion with Bollinger Bands and session gating."""
    transpiler = PineScriptTranspiler()
    code = transpiler.transpile(rsi_reversion_proposal)

    assert "//@version=6" in code
    assert "ta.bb(close, 20, 2.0)" in code
    assert "(rsi_14 <= 30)" in code
    assert "(low <= bb_20_lower)" in code
    assert "(hour >= 8 and hour < 16) or (hour >= 13 and hour < 21)" in code
    assert "session_allowed =" in code


# ==============================================================================
# 2. LONG VS SHORT TRADE DIRECTION
# ==============================================================================

def test_transpile_short_strategy():
    """Verify SHORT strategy mapping to strategy.short and inverted stop/limit prices."""
    short_proposal = StrategyProposal(
        name="XAU_TEST_SHORT_MOMENTUM",
        hypothesis="Breakdown below EMA with high ATR signals sustained downward momentum.",
        direction=TradeDirection.SHORT,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.TRENDING_BEAR],
        indicators=[
            IndicatorSpec(alias="ema_20", name=IndicatorNameEnum.EMA, inputs={"period": 20}),
        ],
        entry_rules=ConditionNode(
            operator="AND",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                    comparator="<",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_20", lag=0),
                )
            ],
        ),
        exit_rules=ConditionNode(
            operator="OR",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                    comparator=">",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_20", lag=0),
                )
            ],
        ),
        stop_loss=StopLossSpec(type="FIXED_POINTS", multiplier_or_value=10.0),
        take_profit=TakeProfitSpec(type="FIXED_POINTS", multiplier_or_value=20.0),
        rationale="Short directional momentum trend following.",
    )

    transpiler = PineScriptTranspiler()
    code = transpiler.transpile(short_proposal)

    assert "strategy.entry(\"XAU_TEST_SHORT_MOMENTUM_Short\", strategy.short)" in code
    assert "strategy.position_size < 0" in code
    # For SHORT, SL is above entry (avg_price + sl_distance) and TP is below entry (avg_price - tp_distance)
    assert "sl_price = strategy.position_avg_price + sl_distance" in code
    assert "tp_price = strategy.position_avg_price - tp_distance" in code


# ==============================================================================
# 3. STOP-LOSS & TAKE-PROFIT MAPPINGS
# ==============================================================================

def test_stop_loss_trailing_atr_mapping():
    """Verify TRAILING_ATR stop loss maps to trail_points in strategy.exit."""
    proposal = StrategyProposal(
        name="XAU_TEST_TRAILING_SL",
        hypothesis="Trailing stop test hypothesis.",
        direction=TradeDirection.LONG,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.TRENDING_BULL],
        indicators=[
            IndicatorSpec(alias="sma_20", name=IndicatorNameEnum.SMA, inputs={"period": 20}),
        ],
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
        stop_loss=StopLossSpec(type="TRAILING_ATR", multiplier_or_value=2.0),
        take_profit=TakeProfitSpec(type="RISK_REWARD_RATIO", multiplier_or_value=3.0),
        rationale="Trailing ATR test with institutional risk controls.",
    )

    transpiler = PineScriptTranspiler()
    code = transpiler.transpile(proposal)

    assert "trail_points=sl_distance / syminfo.mintick" in code
    assert "trail_offset=0" in code


# ==============================================================================
# 4. HISTORICAL LOOKBACK & ZERO LOOKAHEAD
# ==============================================================================

def test_lookback_indexing_translation():
    """Verify that lag > 0 correctly maps to historical index [lag] without lookahead."""
    proposal = StrategyProposal(
        name="XAU_TEST_LAG_INDEX",
        hypothesis="Lookback index test hypothesis.",
        direction=TradeDirection.LONG,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.TRENDING_BULL],
        indicators=[
            IndicatorSpec(alias="rsi_14", name=IndicatorNameEnum.RSI, inputs={"period": 14}),
        ],
        entry_rules=ConditionNode(
            operator="AND",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="rsi_14", lag=2),
                    comparator="<",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="rsi_14", lag=0),
                )
            ],
        ),
        exit_rules=ConditionNode(
            operator="OR",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=1),
                    comparator="<",
                    right=ValueRef(type=ValueRefType.PRICE, name="open", lag=1),
                )
            ],
        ),
        stop_loss=StopLossSpec(type="FIXED_POINTS", multiplier_or_value=5.0),
        take_profit=TakeProfitSpec(type="FIXED_POINTS", multiplier_or_value=10.0),
        rationale="Lag indexing test with historical lookback rules.",
    )

    transpiler = PineScriptTranspiler()
    code = transpiler.transpile(proposal)

    assert "rsi_14[2]" in code
    assert "close[1]" in code
    assert "open[1]" in code
    # Must not contain negative index
    assert "[-" not in code


# ==============================================================================
# 5. UNSUPPORTED AST REJECTION
# ==============================================================================

def test_rejection_unsupported_comparator(dual_ema_proposal):
    """Verify that unsupported comparator raises PineTranspilationError."""
    bad_proposal = dual_ema_proposal.model_copy(deep=True)
    bad_proposal.entry_rules.conditions[0].comparator = "UNKNOWN_OP"

    transpiler = PineScriptTranspiler()
    with pytest.raises(PineTranspilationError) as exc_info:
        transpiler.transpile(bad_proposal)
    assert "Unsupported comparator 'UNKNOWN_OP'" in str(exc_info.value)


def test_rejection_negative_lag_in_transpiler(dual_ema_proposal):
    """Verify that negative lag is intercepted and rejected."""
    bad_proposal = dual_ema_proposal.model_copy(deep=True)
    bad_proposal.entry_rules.conditions[0].left.lag = -2

    transpiler = PineScriptTranspiler()
    with pytest.raises(PineTranspilationError) as exc_info:
        transpiler.transpile(bad_proposal)
    assert "Negative lag -2" in str(exc_info.value)


# ==============================================================================
# 6. DETERMINISTIC REPRODUCIBILITY
# ==============================================================================

def test_deterministic_generation(dual_ema_proposal):
    """Verify that identical AST produces bit-for-bit identical Pine code across runs."""
    transpiler = PineScriptTranspiler()
    code1 = transpiler.transpile(dual_ema_proposal)
    code2 = transpiler.transpile(dual_ema_proposal)

    # Exclude dynamic timestamp line
    clean1 = "\n".join([line for line in code1.splitlines() if not line.startswith("// Transpiled:")])
    clean2 = "\n".join([line for line in code2.splitlines() if not line.startswith("// Transpiled:")])

    assert clean1 == clean2


# ==============================================================================
# 7. STATIC VALIDATION SUITE
# ==============================================================================

def test_static_validation_success(dual_ema_proposal):
    """Verify that transpiled code passes static validation with zero issues."""
    transpiler = PineScriptTranspiler()
    validator = PineStaticValidator()

    code = transpiler.transpile(dual_ema_proposal)
    res: PineValidationResult = validator.validate(code)

    assert res.is_valid is True
    assert res.version == 6
    assert len(res.issues) == 0
    assert "ema" in res.declared_indicators


def test_static_validation_detects_missing_version():
    """Verify that missing //@version directive fails validation."""
    validator = PineStaticValidator()
    code = 'strategy(title="Test")\nif true\n    strategy.entry("L", strategy.long)\n    strategy.exit("E", "L", stop=10)'

    res = validator.validate(code)
    assert res.is_valid is False
    assert any("Missing //@version" in issue for issue in res.issues)


def test_static_validation_detects_wrong_version():
    """Verify that //@version=5 fails when version 6 is required."""
    validator = PineStaticValidator()
    code = '//@version=5\nstrategy(title="Test")\nif true\n    strategy.entry("L", strategy.long)\n    strategy.exit("E", "L", stop=10)'

    res = validator.validate(code)
    assert res.is_valid is False
    assert any("//@version=6 is required" in issue for issue in res.issues)


def test_static_validation_detects_unclosed_delimiters():
    """Verify that unclosed parentheses or brackets fail validation."""
    validator = PineStaticValidator()
    code = '//@version=6\nstrategy(title="Test"\nif true\n    strategy.entry("L", strategy.long)\n    strategy.exit("E", "L", stop=10)'

    res = validator.validate(code)
    assert res.is_valid is False
    assert any("Unclosed delimiters" in issue for issue in res.issues)


def test_static_validation_detects_repainting_lookahead():
    """Verify that barmerge.lookahead_on triggers safety error."""
    validator = PineStaticValidator()
    code = (
        '//@version=6\nstrategy(title="Test")\n'
        'd = request.security(syminfo.tickerid, "D", close, lookahead=barmerge.lookahead_on)\n'
        'if true\n    strategy.entry("L", strategy.long)\n    strategy.exit("E", "L", stop=10)'
    )

    res = validator.validate(code)
    assert res.is_valid is False
    assert any("Dangerous lookahead repainting enabled" in issue for issue in res.issues)


# ==============================================================================
# 8. PYTHON-TO-PINE PARITY VERIFICATION HARNESS
# ==============================================================================

def test_parity_harness_execution(sample_gold_bars, dual_ema_proposal):
    """Verify parity verification harness on Dual EMA crossover strategy."""
    harness = PineParityHarness()
    report, pine_code = harness.verify_parity(dual_ema_proposal, sample_gold_bars)

    assert isinstance(report, ParityReport)
    assert report.strategy_name == dual_ema_proposal.name
    assert report.structural_parity_verified is True
    # In deterministic synthetic trend data, trade count and net pnl should align closely
    assert report.trade_count_delta_pct <= 0.05
    assert report.net_pnl_delta_pct <= 0.05
    assert report.passed_constitutional_parity is True
    assert len(report.limitations) > 0


def test_parity_harness_with_simulated_tradingview_trades(sample_gold_bars, dual_ema_proposal):
    """Verify that external TradingView trade records can be ingested and compared."""
    harness = PineParityHarness()

    # Provide simulated TradingView trade records with minor 2% slippage difference
    mock_pine_trades = [
        {"net_pnl": 150.0},
        {"net_pnl": -80.0},
        {"net_pnl": 220.0},
    ]

    report, _ = harness.verify_parity(
        dual_ema_proposal,
        sample_gold_bars,
        pine_simulated_trades=mock_pine_trades,
    )

    assert report.pine_trade_count == 3
    assert report.pine_net_pnl == 290.0
    assert report.limitations is not None
