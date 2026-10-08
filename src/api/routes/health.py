"""Health and system introspection endpoints."""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.config import settings
from src.database.connection import get_db
from src.api.schemas import HealthResponse, SystemInfoResponse

router = APIRouter(tags=["Health & System"])


@router.get("/health", response_model=HealthResponse)
async def check_health(db: AsyncSession = Depends(get_db)):
    """Comprehensive health check for database, storage, and runtime state."""
    db_status = "HEALTHY"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"UNHEALTHY: {str(e)}"

    storage_ok = settings.DATA_DIR.exists() and settings.NORMALIZED_DATA_DIR.exists()

    return HealthResponse(
        status="OK" if db_status == "HEALTHY" and storage_ok else "DEGRADED",
        phase="PHASE 1 — FOUNDATION + DATA + BACKTESTING",
        database=db_status,
        cache="STANDBY" if "redis" in settings.REDIS_URL else "LOCAL",
        storage_accessible=storage_ok,
        live_trading_enabled=settings.LIVE_TRADING_ENABLED,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/system/info", response_model=SystemInfoResponse)
async def system_info():
    """Retrieve platform configuration and supported instruments."""
    return SystemInfoResponse(
        system_name="Gold AI Quant Research Platform",
        version="1.0.0",
        active_phase="Phase 1",
        environment=settings.ENVIRONMENT,
        instruments=["XAUUSD", "GC=F", "XAUUSD_PERP"],
        live_trading_enabled=settings.LIVE_TRADING_ENABLED,
        paper_trading_enabled=settings.PAPER_TRADING_ENABLED,
    )
