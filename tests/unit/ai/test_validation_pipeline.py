"""Unit tests for WFE mathematical edge cases, Monte Carlo, CSS, cascading early-exit, and async jobs."""

import asyncio
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest

from src.backtest.types import BacktestConfig, BacktestMetrics, BacktestResult, ExitReason, OrderDirection, TradeRecord
from src.quant.scoring import CompositeStrategyScorer
from src.quant.validation.config import ValidationThresholdsConfig
from src.quant.validation.jobs import JobStatus, ValidationJobManager
from src.quant.validation.monte_carlo import MonteCarloSimulator
from src.quant.validation.pipeline import CascadingValidationPipeline
from src.quant.validation.walk_forward import calculate_wfe
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


# ==============================================================================
# 1. WFE MATHEMATICAL EDGE CASES
# ==============================================================================

def test_wfe_normal_passing():
    """R_IS = +0.20, R_OOS = +0.10 -> WFE = 0.5000 (PASS)."""
    wfe = calculate_wfe(is_return=0.20, oos_return=0.10, oos_trades=20)
    assert wfe == 0.5000


def test_wfe_is_zero():
    """R_IS = 0.00 -> WFE = 0.0000 (FAIL: no division by zero)."""
    wfe = calculate_wfe(is_return=0.00, oos_return=0.10, oos_trades=20)
    assert wfe == 0.0000


def test_wfe_is_negative():
    """R_IS = -0.10, R_OOS = -0.05 -> WFE = 0.0000 (FAIL: anti-false-positive)."""
    wfe = calculate_wfe(is_return=-0.10, oos_return=-0.05, oos_trades=20)
    assert wfe == 0.0000


def test_wfe_oos_zero():
    """R_OOS = 0.00 -> WFE = 0.0000 (FAIL)."""
    wfe = calculate_wfe(is_return=0.20, oos_return=0.00, oos_trades=20)
    assert wfe == 0.0000


def test_wfe_oos_negative():
    """R_OOS = -0.05 -> WFE = 0.0000 (FAIL)."""
    wfe = calculate_wfe(is_return=0.20, oos_return=-0.05, oos_trades=20)
    assert wfe == 0.0000


def test_wfe_zero_oos_trades():
    """N_trades_OOS == 0 -> WFE = 0.0000 (FAIL: insufficient sample)."""
    wfe = calculate_wfe(is_return=0.20, oos_return=0.10, oos_trades=0)
    assert wfe == 0.0000


def test_wfe_nan_and_inf():
    """NaN and Inf returns -> WFE = 0.0000 (FAIL)."""
    assert calculate_wfe(float("nan"), 0.10, 20) == 0.0000
    assert calculate_wfe(0.20, float("nan"), 20) == 0.0000
    assert calculate_wfe(float("inf"), 0.10, 20) == 0.0000
    assert calculate_wfe(0.20, float("inf"), 20) == 0.0000


# ==============================================================================
# 2. MONTE CARLO ROBUSTNESS & FRICTION STRESS
# ==============================================================================

def create_synthetic_trades(n_trades: int = 50, win_pct: float = 0.60):
    trades = []
    for i in range(n_trades):
        is_win = (i / n_trades) < win_pct
        pnl = 150.0 if is_win else -100.0
        trades.append(
            TradeRecord(
                trade_number=i + 1,
                direction=OrderDirection.LONG,
                entry_time=datetime.now(timezone.utc),
                exit_time=datetime.now(timezone.utc),
                entry_price=2000.0,
                exit_price=2015.0 if is_win else 1990.0,
                size=1.0,
                initial_sl=1990.0,
                initial_tp=2015.0,
                gross_pnl=pnl + 5.0,
                net_pnl=pnl,
                return_pct=pnl / 10000.0,
                r_multiple=1.5 if is_win else -1.0,
                fees=2.0,
                slippage=3.0,
                mae_pct=0.005,
                mfe_pct=0.015,
                exit_reason=ExitReason.TAKE_PROFIT if is_win else ExitReason.STOP_LOSS,
                holding_bars=10,
            )
        )
    return trades


def test_monte_carlo_bootstrap():
    simulator = MonteCarloSimulator()
    trades = create_synthetic_trades(n_trades=60, win_pct=0.65)
    res = simulator.evaluate(trades=trades, iterations=500)

    assert res.iterations == 500
    assert res.median_drawdown_pct >= 0.0
    assert res.probability_of_ruin <= 0.05
    assert res.stress_profit_factor > 1.0


# ==============================================================================
# 3. COMPOSITE STRATEGY SCORER (CSS)
# ==============================================================================

def test_css_scoring_components():
    scorer = CompositeStrategyScorer()
    res = scorer.compute_score(
        sortino_oos=3.0,
        max_dd_oos=0.03,
        gpf_oos=2.50,
        wfe=0.90,
        p95_dd_mc=0.04,
        degradation=0.05,
        passed_hard_gates=True,
    )

    assert res.css_score >= 80.0
    assert res.tier == "TIER_1"
    assert res.passed_hard_gates is True


def test_css_hard_gate_veto():
    """Hard gate failure forces CSS to 0.0 and DISQUALIFIED."""
    scorer = CompositeStrategyScorer()
    res = scorer.compute_score(
        sortino_oos=3.0,
        max_dd_oos=0.05,
        gpf_oos=2.50,
        wfe=0.90,
        p95_dd_mc=0.05,
        degradation=0.05,
        passed_hard_gates=False,  # Hard gate failed!
    )

    assert res.css_score == 0.00
    assert res.tier == "DISQUALIFIED"
    assert res.passed_hard_gates is False


# ==============================================================================
# 4. CASCADING EARLY-EXIT SHORT-CIRCUITING
# ==============================================================================

def test_cascading_pipeline_early_exit_on_is_failure():
    """If IS hard gates fail, WFA and Monte Carlo MUST NOT run."""
    pipeline = CascadingValidationPipeline(
        config=ValidationThresholdsConfig(MIN_TRADES_GATE=50)
    )

    # Small 15-bar dataframe where fewer than 50 trades will occur
    dates = pd.date_range("2024-01-01", periods=15, freq="1h", tz=timezone.utc)
    closes = np.full(15, 2000.0)
    df = pd.DataFrame(
        {
            "timestamp": dates,
            "open": closes,
            "high": closes + 1.0,
            "low": closes - 1.0,
            "close": closes,
            "volume": np.full(15, 100.0),
        }
    )

    proposal = StrategyProposal(
        name="XAU_TEST_EARLY_EXIT",
        hypothesis="Strategy that fails trade count gate and short-circuits early.",
        direction=TradeDirection.LONG,
        instrument=InstrumentEnum.XAUUSD,
        timeframe=TimeframeEnum.H1,
        data_source=DataSourceEnum.DUKASCOPY,
        target_regimes=[RegimeEnum.RANGING],
        indicators=[IndicatorSpec(alias="ema_10", name=IndicatorNameEnum.EMA, inputs={"period": 10})],
        entry_rules=ConditionNode(
            operator="AND",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                    comparator=">",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_10", lag=0),
                )
            ],
        ),
        exit_rules=ConditionNode(
            operator="OR",
            conditions=[
                ConditionComparison(
                    left=ValueRef(type=ValueRefType.PRICE, name="close", lag=0),
                    comparator="<",
                    right=ValueRef(type=ValueRefType.INDICATOR, name="ema_10", lag=0),
                )
            ],
        ),
        stop_loss=StopLossSpec(type="ATR_MULTIPLE", multiplier_or_value=1.5),
        take_profit=TakeProfitSpec(type="ATR_MULTIPLE", multiplier_or_value=3.0),
        rationale="Early exit verification proposal.",
    )

    report = pipeline.run_pipeline(proposal, df)

    assert report.overall_passed is False
    assert report.final_tier == "REJECTED"
    # Ensure WFA and Monte Carlo never executed
    assert report.wfa_result is None
    assert report.monte_carlo_result is None
    assert report.score_result is None


# ==============================================================================
# 5. ASYNC VALIDATION JOBS
# ==============================================================================

@pytest.mark.asyncio
async def test_async_validation_job_lifecycle():
    manager = ValidationJobManager()
    job = manager.create_job(job_type="WALK_FORWARD")

    assert job.status == JobStatus.PENDING
    assert job.progress_pct == 0.0

    async def sample_task():
        await asyncio.sleep(0.01)
        return {"mean_wfe": 0.62, "passed": True}

    await manager.run_async_task(job.job_id, sample_task)

    updated_job = manager.get_job(job.job_id)
    assert updated_job is not None
    assert updated_job.status == JobStatus.COMPLETED
    assert updated_job.progress_pct == 100.0
    assert updated_job.result == {"mean_wfe": 0.62, "passed": True}
