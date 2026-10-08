"""Determinism tests for Phase 2: Reproducibility of AST signals, Monte Carlo, and CSS."""

from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest

from src.ai.providers.base import LLMProvider
from src.ai.providers.mock import MockLLMProvider
from src.backtest.types import ExitReason, OrderDirection, TradeRecord
from src.quant.scoring import CompositeStrategyScorer
from src.quant.validation.monte_carlo import MonteCarloSimulator
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
def test_dataset():
    n_bars = 150
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="1h", tz=timezone.utc)
    closes = 2000.0 + np.sin(np.linspace(0, 10, n_bars)) * 15.0
    return pd.DataFrame(
        {
            "timestamp": dates,
            "open": closes - 0.5,
            "high": closes + 1.5,
            "low": closes - 1.5,
            "close": closes,
            "volume": np.full(n_bars, 500.0),
        }
    )


def test_deterministic_signal_generation(test_dataset):
    """Running identical ASTStrategy on identical dataset produces bit-for-bit identical signals."""
    proposal = StrategyProposal(
        name="XAU_DETERMINISTIC_TEST",
        hypothesis="Deterministic signal generation test.",
        direction=TradeDirection.LONG,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.RANGING],
        indicators=[
            IndicatorSpec(alias="rsi_14", name=IndicatorNameEnum.RSI, inputs={"period": 14}),
        ],
        entry_rules=ConditionNode(
            operator="AND",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="rsi_14", lag=0),
                    comparator="<",
                    right=ValueRef(type=ValueRefType.CONSTANT, value=30.0, lag=0),
                )
            ],
        ),
        exit_rules=ConditionNode(
            operator="OR",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.INDICATOR, name="rsi_14", lag=0),
                    comparator=">",
                    right=ValueRef(type=ValueRefType.CONSTANT, value=70.0, lag=0),
                )
            ],
        ),
        stop_loss=StopLossSpec(type="ATR_MULTIPLE", multiplier_or_value=1.5),
        take_profit=TakeProfitSpec(type="ATR_MULTIPLE", multiplier_or_value=3.0),
        rationale="Determinism validation.",
    )

    strat_1 = ASTStrategy(proposal=proposal)
    strat_2 = ASTStrategy(proposal=proposal)

    signals_1 = strat_1.generate_signals(test_dataset)
    signals_2 = strat_2.generate_signals(test_dataset)

    assert len(signals_1) == len(signals_2)
    for s1, s2 in zip(signals_1, signals_2):
        assert s1.timestamp == s2.timestamp
        assert s1.direction == s2.direction
        assert s1.entry_price == s2.entry_price
        assert s1.stop_loss == s2.stop_loss
        assert s1.take_profit == s2.take_profit


def test_deterministic_monte_carlo_seed():
    """Pinned seed produces bit-for-bit identical Monte Carlo statistics."""
    trades = [
        TradeRecord(
            trade_number=i,
            direction=OrderDirection.LONG,
            entry_time=datetime.now(timezone.utc),
            exit_time=datetime.now(timezone.utc),
            entry_price=2000.0,
            exit_price=2010.0 if i % 2 == 0 else 1995.0,
            size=1.0,
            initial_sl=1995.0,
            initial_tp=2010.0,
            gross_pnl=100.0 if i % 2 == 0 else -50.0,
            net_pnl=95.0 if i % 2 == 0 else -55.0,
            return_pct=0.01 if i % 2 == 0 else -0.005,
            r_multiple=2.0 if i % 2 == 0 else -1.0,
            fees=2.0,
            slippage=3.0,
            mae_pct=0.002,
            mfe_pct=0.005,
            exit_reason=ExitReason.TAKE_PROFIT if i % 2 == 0 else ExitReason.STOP_LOSS,
            holding_bars=5,
        )
        for i in range(25)
    ]

    mc = MonteCarloSimulator()
    run_1 = mc.run_bootstrap_reshuffle(trades=trades, iterations=500, seed=12345)
    run_2 = mc.run_bootstrap_reshuffle(trades=trades, iterations=500, seed=12345)

    assert run_1["median_drawdown_pct"] == run_2["median_drawdown_pct"]
    assert run_1["p95_drawdown_pct"] == run_2["p95_drawdown_pct"]
    assert run_1["worst_case_drawdown_pct"] == run_2["worst_case_drawdown_pct"]
    assert run_1["probability_of_ruin"] == run_2["probability_of_ruin"]


def test_deterministic_prompt_hashing():
    """Identical prompt strings produce identical SHA-256 hashes."""
    provider = MockLLMProvider()
    hash_1 = provider.compute_prompt_hash("Analyze Gold", "System Persona")
    hash_2 = provider.compute_prompt_hash("Analyze Gold", "System Persona")
    assert hash_1 == hash_2
    assert len(hash_1) == 64

