"""FastAPI route handlers for Phase 2 AI quantitative research and asynchronous validation jobs."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ai.agents import (
    MarketAnalystAgent,
    RegimeAnalystAgent,
    StrategyResearcherAgent,
    QuantValidatorAgent,
    PerformanceAnalystAgent,
)
from src.ai.providers import MockLLMProvider, OllamaProvider
from src.ai.summarizer import MarketBrief, MarketFeatureSummarizer, StrategyResearchContext
from src.database.connection import get_db
from src.database.models import (
    AgentDecision,
    Backtest,
    ResearchHypothesis,
    ResearchSession,
    Strategy,
    StrategyVersion,
    ValidationResult,
)
import numpy as np
import pandas as pd
from src.data.storage.parquet_manager import ParquetStorageManager
from src.quant.scoring import CompositeStrategyScorer
from src.quant.validation.config import DEFAULT_VALIDATION_CONFIG
from src.quant.validation.jobs import JobStatus, job_manager
from src.quant.validation.monte_carlo import MonteCarloSimulator
from src.quant.validation.pipeline import CascadingValidationPipeline
from src.quant.validation.walk_forward import WalkForwardAnalyzer
from src.strategy.contracts import RegimeEnum, StrategyProposal, TradeDirection


def get_or_create_research_data(instrument: str = "XAUUSD", timeframe: str = "1h") -> pd.DataFrame:
    """Load candles from Parquet or generate deterministic fallback data."""
    storage = ParquetStorageManager()
    try:
        df = storage.load_candles(instrument=instrument, timeframe=timeframe)
        if not df.empty:
            return df
    except Exception:
        pass

    # Deterministic fallback for testing if no parquet file on disk
    n_bars = 200
    tf_freq_map = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1D",
    }
    freq = tf_freq_map.get(timeframe, "1h")
    dates = pd.date_range("2024-01-01", periods=n_bars, freq=freq, tz=timezone.utc)
    base_price = 2000.0 if ("XAU" in instrument or "GC" in instrument) else 100.0
    trend = np.linspace(0, 40, n_bars)
    noise = np.sin(np.linspace(0, 20, n_bars)) * 3.0
    closes = base_price + trend + noise
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


router = APIRouter(prefix="/research", tags=["AI Research & Validation Engine"])


# ==============================================================================
# SCHEMAS
# ==============================================================================

class ResearchSessionCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=128)
    target_regime: str = Field(default="ALL")
    timeframe: str = Field(default="1h")
    instrument: str = Field(default="XAUUSD")
    max_iterations: int = Field(default=10, ge=1, le=20)


class ResearchSessionResponse(BaseModel):
    id: str
    name: str
    status: str
    target_regime: str
    timeframe: str
    instrument: str
    iterations_count: int
    max_iterations: int
    created_at: str


class HypothesisCreate(BaseModel):
    thesis_statement: str = Field(..., min_length=10)
    economic_rationale: str = Field(..., min_length=10)
    expected_edge: str = Field(..., min_length=10)


class HypothesisResponse(BaseModel):
    id: str
    session_id: str
    thesis_statement: str
    economic_rationale: str
    expected_edge: str
    status: str
    created_at: str


class JobSubmitResponse(BaseModel):
    job_id: str
    status: str
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    job_type: str
    status: str
    progress_pct: float
    stage: str
    error: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


class ScoreCalculateRequest(BaseModel):
    sortino_oos: float
    max_dd_oos: float
    gpf_oos: float
    wfe: float
    p95_dd_mc: float
    degradation: float
    passed_hard_gates: bool = True


class LeaderboardEntry(BaseModel):
    strategy_id: str
    strategy_name: str
    css_score: float
    tier: str
    profit_factor: float
    win_rate_pct: float
    max_drawdown_pct: float


# ==============================================================================
# SESSIONS & HYPOTHESES
# ==============================================================================

@router.post("/sessions", response_model=ResearchSessionResponse)
async def create_research_session(
    body: ResearchSessionCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new AI research session."""
    session_id = str(uuid4())
    now = datetime.now(timezone.utc)
    session = ResearchSession(
        id=session_id,
        name=body.name,
        target_regime=body.target_regime,
        timeframe=body.timeframe,
        instrument=body.instrument,
        max_iterations=body.max_iterations,
        iterations_count=0,
        status="ACTIVE",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    return ResearchSessionResponse(
        id=session.id,
        name=session.name,
        status=session.status,
        target_regime=session.target_regime,
        timeframe=session.timeframe,
        instrument=session.instrument,
        iterations_count=session.iterations_count,
        max_iterations=session.max_iterations,
        created_at=session.created_at.isoformat(),
    )


@router.get("/sessions/{session_id}", response_model=ResearchSessionResponse)
async def get_research_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Fetch research session details by ID."""
    result = await db.execute(select(ResearchSession).where(ResearchSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail=f"Research session '{session_id}' not found.")

    return ResearchSessionResponse(
        id=session.id,
        name=session.name,
        status=session.status,
        target_regime=session.target_regime,
        timeframe=session.timeframe,
        instrument=session.instrument,
        iterations_count=session.iterations_count,
        max_iterations=session.max_iterations,
        created_at=session.created_at.isoformat(),
    )


@router.post("/sessions/{session_id}/hypotheses", response_model=HypothesisResponse)
async def create_hypothesis(
    session_id: str,
    body: HypothesisCreate,
    db: AsyncSession = Depends(get_db),
):
    """Pre-register a falsifiable quantitative hypothesis under an active session."""
    sess_res = await db.execute(select(ResearchSession).where(ResearchSession.id == session_id))
    session = sess_res.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail=f"Research session '{session_id}' not found.")

    hypo_id = str(uuid4())
    now = datetime.now(timezone.utc)
    hypo = ResearchHypothesis(
        id=hypo_id,
        session_id=session_id,
        thesis_statement=body.thesis_statement,
        economic_rationale=body.economic_rationale,
        expected_edge=body.expected_edge,
        status="FORMULATED",
        created_at=now,
        updated_at=now,
    )
    db.add(hypo)
    await db.commit()
    await db.refresh(hypo)

    return HypothesisResponse(
        id=hypo.id,
        session_id=hypo.session_id,
        thesis_statement=hypo.thesis_statement,
        economic_rationale=hypo.economic_rationale,
        expected_edge=hypo.expected_edge,
        status=hypo.status,
        created_at=hypo.created_at.isoformat(),
    )


# ==============================================================================
# VALIDATION & ASYNC JOBS
# ==============================================================================

@router.post("/strategies/validate")
async def validate_strategy_synchronous(
    body: Dict[str, Any],
):
    """Execute synchronous In-Sample hard gate validation on a proposed strategy AST."""
    try:
        proposal = StrategyProposal.model_validate(body)
    except Exception as err:
        raise HTTPException(status_code=422, detail=f"Invalid StrategyProposal AST schema: {err}")

    inst = proposal.instrument.value
    tf = proposal.timeframe.value
    df = get_or_create_research_data(instrument=inst, timeframe=tf)
    pipeline = CascadingValidationPipeline()
    report = pipeline.run_pipeline(proposal, df)

    return report.to_dict()


@router.post("/strategies/walk-forward", response_model=JobSubmitResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_walk_forward_job(
    body: Dict[str, Any],
    background_tasks: BackgroundTasks,
):
    """Submit rolling Walk-Forward Analysis as a non-blocking asynchronous job."""
    try:
        proposal = StrategyProposal.model_validate(body)
    except Exception as err:
        raise HTTPException(status_code=422, detail=f"Invalid StrategyProposal AST schema: {err}")

    inst = proposal.instrument.value
    tf = proposal.timeframe.value
    df = get_or_create_research_data(instrument=inst, timeframe=tf)
    from src.strategy.interpreter import ASTStrategy
    strategy = ASTStrategy(proposal)

    analyzer = WalkForwardAnalyzer()
    job = job_manager.create_job(job_type="WALK_FORWARD")

    def run_wfa():
        res = analyzer.evaluate_strategy(df=df, strategy=strategy, instrument=inst, timeframe=tf)
        return res.to_dict()

    background_tasks.add_task(job_manager.run_async_task, job.job_id, run_wfa)

    return JobSubmitResponse(
        job_id=job.job_id,
        status=job.status.value,
        message="Walk-Forward Analysis job submitted successfully. Poll /research/jobs/{job_id} for status.",
    )


@router.post("/strategies/monte-carlo", response_model=JobSubmitResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_monte_carlo_job(
    body: Dict[str, Any],
    background_tasks: BackgroundTasks,
):
    """Submit Monte Carlo simulation (2,000 paths) as a non-blocking asynchronous job."""
    try:
        proposal = StrategyProposal.model_validate(body)
    except Exception as err:
        raise HTTPException(status_code=422, detail=f"Invalid StrategyProposal AST schema: {err}")

    inst = proposal.instrument.value
    tf = proposal.timeframe.value
    df = get_or_create_research_data(instrument=inst, timeframe=tf)

    from src.strategy.interpreter import ASTStrategy
    from src.backtest.engine import BacktestEngine
    strategy = ASTStrategy(proposal)
    engine = BacktestEngine()
    backtest_res = engine.run(df, strategy, instrument=inst, timeframe=tf)

    mc = MonteCarloSimulator()
    job = job_manager.create_job(job_type="MONTE_CARLO")

    def run_mc():
        res = mc.evaluate(trades=backtest_res.trades, iterations=2000)
        return res.to_dict()

    background_tasks.add_task(job_manager.run_async_task, job.job_id, run_mc)

    return JobSubmitResponse(
        job_id=job.job_id,
        status=job.status.value,
        message="Monte Carlo job submitted successfully. Poll /research/jobs/{job_id} for status.",
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Poll asynchronous validation job execution progress and status."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return JobStatusResponse(**job.to_dict())


@router.get("/jobs/{job_id}/result")
async def get_job_result(job_id: str):
    """Fetch result payload of a completed asynchronous validation job."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail=f"Job '{job_id}' is in '{job.status.value}' state. Result is only available when COMPLETED.",
        )
    return job.result


# ==============================================================================
# SCORING & LEADERBOARD
# ==============================================================================

@router.post("/strategies/score")
async def calculate_composite_score(body: ScoreCalculateRequest):
    """Calculate deterministic Composite Strategy Score (CSS)."""
    scorer = CompositeStrategyScorer()
    res = scorer.compute_score(
        sortino_oos=body.sortino_oos,
        max_dd_oos=body.max_dd_oos,
        gpf_oos=body.gpf_oos,
        wfe=body.wfe,
        p95_dd_mc=body.p95_dd_mc,
        degradation=body.degradation,
        passed_hard_gates=body.passed_hard_gates,
    )
    return res.to_dict()


@router.get("/leaderboard", response_model=List[LeaderboardEntry])
async def get_research_leaderboard(
    db: AsyncSession = Depends(get_db),
):
    """Retrieve ranked list of validated strategy candidates from verified database records (P2-3 Resolution)."""
    stmt = (
        select(StrategyVersion, Backtest, ValidationResult)
        .join(Backtest, Backtest.strategy_version_id == StrategyVersion.id)
        .join(ValidationResult, ValidationResult.backtest_id == Backtest.id)
        .order_by(ValidationResult.robustness_score.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    leaderboard = []
    seen_versions = set()
    for version, backtest, val_res in rows:
        if version.id in seen_versions:
            continue
        seen_versions.add(version.id)

        css = round(float(val_res.robustness_score), 2)
        if css >= 80.0:
            tier = "TIER_1"
        elif css >= 70.0:
            tier = "TIER_2"
        else:
            tier = "DISQUALIFIED"

        pf = round(float(val_res.oos_profit_factor if val_res.oos_profit_factor is not None else (backtest.profit_factor or 0.0)), 2)
        wr = round(float(backtest.win_rate_pct or 0.0), 2)
        dd = round(float(val_res.oos_drawdown_pct if val_res.oos_drawdown_pct is not None else (backtest.max_drawdown_pct or 0.0)), 2)

        leaderboard.append(
            LeaderboardEntry(
                strategy_id=version.strategy_id,
                strategy_name=f"{version.strategy_id}_{version.version_tag}",
                css_score=css,
                tier=tier,
                profit_factor=pf,
                win_rate_pct=wr,
                max_drawdown_pct=dd,
            )
        )
    return sorted(leaderboard, key=lambda x: x.css_score, reverse=True)


@router.get("/audit/{session_id}")
async def get_agent_audit_trail(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Retrieve cryptographic AI audit trail for a research session."""
    result = await db.execute(select(AgentDecision))
    decisions = result.scalars().all()

    return [
        {
            "id": d.id,
            "agent_name": d.agent_name,
            "input_context_summary": d.input_context_summary,
            "reasoning_output": d.reasoning_output,
            "created_at": d.created_at.isoformat(),
        }
        for d in decisions
    ]
