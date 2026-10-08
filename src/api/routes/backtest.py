"""Deterministic backtest execution and results endpoints."""

from datetime import datetime, timezone
import uuid
from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.logger import logger
from src.data.storage.parquet_manager import ParquetStorageManager
from src.strategy.registry import StrategyRegistry
from src.backtest.engine import BacktestEngine
from src.backtest.types import BacktestConfig
from src.database.connection import get_db
from src.database.models import Backtest, BacktestTrade, Strategy, StrategyVersion
from src.api.schemas import BacktestRunRequest, BacktestRunResponse

router = APIRouter(prefix="/backtest", tags=["Backtesting"])

parquet_mgr = ParquetStorageManager()


@router.post("/run", response_model=BacktestRunResponse)
async def run_backtest(req: BacktestRunRequest, db: AsyncSession = Depends(get_db)):
    """Execute a deterministic backtest against validated historical Gold data."""
    # 1. Load dataset
    try:
        df = parquet_mgr.load_candles(instrument=req.instrument, timeframe=req.timeframe)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Historical dataset for {req.instrument} [{req.timeframe}] not found. Ingest data first via /data/ingest.",
        )

    # 2. Instantiate strategy
    try:
        strategy_instance = StrategyRegistry.get(req.strategy_id, req.parameters)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid strategy parameters: {str(e)}")

    # 3. Configure and execute backtest
    config = BacktestConfig(
        initial_capital=req.initial_capital,
        risk_per_trade_pct=req.risk_per_trade_pct,
        spread_usd=req.spread_usd,
        slippage_usd=req.slippage_usd,
    )
    engine = BacktestEngine(config)
    result = engine.run(df=df, strategy=strategy_instance, instrument=req.instrument, timeframe=req.timeframe)

    # 4. Persist to Database
    backtest_id = str(uuid.uuid4())

    # Ensure strategy and strategy_version records exist in DB
    strat_q = await db.execute(select(Strategy).where(Strategy.id == strategy_instance.strategy_id))
    db_strat = strat_q.scalar_one_or_none()
    if not db_strat:
        db_strat = Strategy(
            id=strategy_instance.strategy_id,
            name=strategy_instance.name,
            category=strategy_instance.category,
            description=strategy_instance.description,
            author="QUANT_ENGINEER",
        )
        db.add(db_strat)
        await db.flush()

    ver_q = await db.execute(
        select(StrategyVersion).where(
            StrategyVersion.strategy_id == strategy_instance.strategy_id,
            StrategyVersion.version_tag == strategy_instance.version_tag,
        )
    )
    db_ver = ver_q.scalar_one_or_none()
    if not db_ver:
        db_ver = StrategyVersion(
            id=str(uuid.uuid4()),
            strategy_id=strategy_instance.strategy_id,
            version_tag=strategy_instance.version_tag,
            timeframe=req.timeframe,
            status="BACKTESTED",
            logic_description=strategy_instance.description,
        )
        db.add(db_ver)
        await db.flush()

    # Save Backtest model
    m = result.metrics
    db_backtest = Backtest(
        id=backtest_id,
        strategy_version_id=db_ver.id,
        instrument_id=req.instrument,
        timeframe=req.timeframe,
        start_date=df["timestamp"].iloc[0].to_pydatetime(),
        end_date=df["timestamp"].iloc[-1].to_pydatetime(),
        initial_capital=m.initial_capital,
        final_equity=m.final_equity,
        total_net_profit=m.total_net_profit,
        total_return_pct=m.total_return_pct,
        cagr_pct=m.cagr_pct,
        sharpe_ratio=m.sharpe_ratio,
        sortino_ratio=m.sortino_ratio,
        profit_factor=m.profit_factor,
        win_rate_pct=m.win_rate_pct,
        max_drawdown_pct=m.max_drawdown_pct,
        max_drawdown_usd=m.max_drawdown_usd,
        recovery_factor=m.recovery_factor,
        expectancy_usd=m.expectancy_usd,
        average_r=m.average_r,
        total_trades=m.total_trades,
        winning_trades=m.winning_trades,
        losing_trades=m.losing_trades,
        max_consecutive_losses=m.max_consecutive_losses,
        max_adverse_excursion_avg=m.max_adverse_excursion_avg,
        max_favorable_excursion_avg=m.max_favorable_excursion_avg,
        total_fees_paid=m.total_fees_paid,
        total_slippage_cost=m.total_slippage_cost,
        execution_assumptions=result.execution_assumptions,
    )
    db.add(db_backtest)

    # Save Trades
    for t in result.trades:
        db_trade = BacktestTrade(
            id=str(uuid.uuid4()),
            backtest_id=backtest_id,
            trade_number=t.trade_number,
            direction=t.direction.value,
            entry_time=t.entry_time,
            exit_time=t.exit_time,
            entry_price=t.entry_price,
            exit_price=t.exit_price,
            size=t.size,
            initial_stop_loss=t.initial_sl,
            initial_take_profit=t.initial_tp,
            gross_pnl=t.gross_pnl,
            net_pnl=t.net_pnl,
            return_pct=t.return_pct,
            r_multiple=t.r_multiple,
            fees=t.fees,
            slippage=t.slippage,
            mae_pct=t.mae_pct,
            mfe_pct=t.mfe_pct,
            exit_reason=t.exit_reason.value,
        )
        db.add(db_trade)

    await db.commit()

    return BacktestRunResponse(
        backtest_id=backtest_id,
        strategy_id=result.strategy_id,
        instrument=result.instrument,
        timeframe=result.timeframe,
        start_date=result.start_date,
        end_date=result.end_date,
        metrics=result.metrics.to_dict(),
        total_trades=len(result.trades),
        execution_assumptions=result.execution_assumptions,
        trades=[t.to_dict() for t in result.trades],
        equity_curve=result.equity_curve,
    )


@router.get("/{backtest_id}")
async def get_backtest_by_id(backtest_id: str, db: AsyncSession = Depends(get_db)):
    """Retrieve historical backtest results and trade log from database."""
    res = await db.execute(select(Backtest).where(Backtest.id == backtest_id))
    bt = res.scalar_one_or_none()
    if not bt:
        raise HTTPException(status_code=404, detail="Backtest not found.")

    trades_res = await db.execute(select(BacktestTrade).where(BacktestTrade.backtest_id == backtest_id).order_by(BacktestTrade.trade_number))
    trades = trades_res.scalars().all()

    return {
        "backtest_id": bt.id,
        "instrument": bt.instrument_id,
        "timeframe": bt.timeframe,
        "start_date": bt.start_date.isoformat(),
        "end_date": bt.end_date.isoformat(),
        "total_net_profit": bt.total_net_profit,
        "total_return_pct": bt.total_return_pct,
        "sharpe_ratio": bt.sharpe_ratio,
        "max_drawdown_pct": bt.max_drawdown_pct,
        "win_rate_pct": bt.win_rate_pct,
        "total_trades": bt.total_trades,
        "trades": [
            {
                "trade_number": t.trade_number,
                "direction": t.direction,
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat(),
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "size": t.size,
                "net_pnl": t.net_pnl,
                "r_multiple": t.r_multiple,
                "exit_reason": t.exit_reason,
            }
            for t in trades
        ],
    }
