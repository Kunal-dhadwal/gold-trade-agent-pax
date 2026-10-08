"""Market data ingestion, status, and query endpoints."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.config import settings
from src.core.logger import logger
from src.data.sources.dukascopy import DukascopyClient
from src.data.sources.yahoo import YahooFinanceClient
from src.data.sources.delta import DeltaExchangeClient
from src.data.validation.quality_engine import DataQualityEngine
from src.data.storage.parquet_manager import ParquetStorageManager
from src.data.resampler import TimeframeResampler
from src.quant.regime.detector import MarketRegimeDetector
from src.database.connection import get_db
from src.database.models import MarketDataMetadata
from src.api.schemas import IngestRequest, IngestResponse, CandleListResponse, CandleItem

router = APIRouter(prefix="/data", tags=["Market Data"])

parquet_mgr = ParquetStorageManager()
quality_engine = DataQualityEngine()


@router.post("/ingest", response_model=IngestResponse)
async def ingest_market_data(req: IngestRequest, db: AsyncSession = Depends(get_db)):
    """Ingest, validate, clean, and persist market data."""
    logger.info(f"Ingestion requested: {req.symbol} via {req.source} [{req.timeframe}]")

    # 1. Fetch raw data
    try:
        if req.source.upper() in ("DUKASCOPY", "DUKAS"):
            client = DukascopyClient()
            raw_df = await client.fetch_candles(
                symbol=req.symbol,
                timeframe=req.timeframe,
                start_time=req.start_time,
                end_time=req.end_time,
            )
        elif req.source.upper() == "YAHOO":
            client = YahooFinanceClient()
            raw_df = await client.fetch_candles(
                symbol=req.symbol,
                timeframe=req.timeframe,
                start_time=req.start_time,
                end_time=req.end_time,
                range_str=req.range_str,
            )
        elif req.source.upper() in ("DELTA", "DELTA_EXCHANGE"):
            client = DeltaExchangeClient()
            raw_df = await client.fetch_candles(
                symbol=req.symbol,
                timeframe=req.timeframe,
                start_time=req.start_time,
                end_time=req.end_time,
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported data source: {req.source}")
    except Exception as e:
        logger.error(f"Data ingestion fetch failed: {e}")
        raise HTTPException(status_code=502, detail=f"Failed to fetch market data from {req.source}: {str(e)}")

    # 2. Validate and Clean
    clean_df, report = quality_engine.validate_and_clean(
        df=raw_df,
        instrument=req.symbol,
        source=req.source.upper(),
    )

    if clean_df.empty:
        raise HTTPException(status_code=422, detail="No valid bars survived quality filtering.")

    # 3. Store Parquet
    saved_path = parquet_mgr.save_candles(
        df=clean_df,
        instrument=req.symbol,
        timeframe=req.timeframe,
        source=req.source.upper(),
        quality_score=report.quality_score,
    )

    # If 1m data ingested, auto-generate resampled timeframes
    if req.timeframe == "1m":
        resampled_map = TimeframeResampler.generate_all_timeframes(clean_df)
        for tf, r_df in resampled_map.items():
            if not r_df.empty:
                parquet_mgr.save_candles(
                    df=r_df,
                    instrument=req.symbol,
                    timeframe=tf,
                    source=f"{req.source}_RESAMPLED",
                    is_resampled=True,
                )

    # 4. Save metadata to DB
    meta_rec = MarketDataMetadata(
        instrument_id=req.symbol,
        source=req.source.upper(),
        timeframe=req.timeframe,
        start_time=clean_df["timestamp"].iloc[0].to_pydatetime(),
        end_time=clean_df["timestamp"].iloc[-1].to_pydatetime(),
        candle_count=len(clean_df),
        file_path=str(saved_path),
        file_size_bytes=saved_path.stat().st_size,
        checksum_sha256=parquet_mgr._compute_sha256(saved_path),
        quality_score=report.quality_score,
        is_normalized=True,
    )
    db.add(meta_rec)
    await db.commit()

    return IngestResponse(
        success=True,
        instrument=req.symbol,
        source=req.source.upper(),
        timeframe=req.timeframe,
        bars_ingested=len(clean_df),
        quality_score=report.quality_score,
        file_path=str(saved_path),
        checksum_sha256=meta_rec.checksum_sha256,
        date_range_start=clean_df["timestamp"].iloc[0].isoformat(),
        date_range_end=clean_df["timestamp"].iloc[-1].isoformat(),
    )


@router.get("/status")
async def get_datasets_status():
    """List all persisted datasets, timeframes, and metrics."""
    return {"datasets": parquet_mgr.list_datasets()}


@router.get("/quality-reports")
async def list_quality_reports():
    """List generated data quality audit reports."""
    reports = []
    for f in settings.QUALITY_REPORTS_DIR.glob("quality_*.json"):
        try:
            with open(f, "r", encoding="utf-8") as rf:
                reports.append(json.load(rf))
        except Exception:
            continue
    return {"reports": reports}


@router.get("/candles", response_model=CandleListResponse)
async def get_candles(
    instrument: str = Query("GC=F", description="Symbol name"),
    timeframe: str = Query("1h", description="Candle timeframe"),
    limit: int = Query(500, le=5000),
    include_regime: bool = Query(True),
):
    """Retrieve normalized OHLCV candles with optional regime tags."""
    try:
        df = parquet_mgr.load_candles(instrument=instrument, timeframe=timeframe)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No dataset found for {instrument} [{timeframe}]. Ingest data first.")

    if include_regime:
        df = MarketRegimeDetector.classify_series(df)

    tail_df = df.tail(limit)
    items = []
    for _, row in tail_df.iterrows():
        items.append(CandleItem(
            timestamp=row["timestamp"].isoformat(),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            regime=str(row.get("regime", "UNKNOWN")),
        ))

    return CandleListResponse(
        instrument=instrument,
        timeframe=timeframe,
        total_bars=len(items),
        candles=items,
    )
