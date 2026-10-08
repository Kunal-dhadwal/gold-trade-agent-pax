"""Pydantic schemas for FastAPI REST endpoints."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    phase: str
    database: str
    cache: str
    storage_accessible: bool
    live_trading_enabled: bool
    timestamp: str


class SystemInfoResponse(BaseModel):
    system_name: str
    version: str
    active_phase: str
    environment: str
    instruments: List[str]
    live_trading_enabled: bool
    paper_trading_enabled: bool


class IngestRequest(BaseModel):
    source: str = Field(default="DUKASCOPY", description="DUKASCOPY, YAHOO, DELTA_EXCHANGE, or LOCAL_ARCHIVE")
    symbol: str = Field(default="XAUUSD", description="Instrument symbol (XAUUSD, GC=F, PAXGUSDT)")
    timeframe: str = Field(default="1h", description="1m, 5m, 15m, 30m, 1h, 4h, 1d")
    range_str: Optional[str] = Field(default="1mo", description="Range string (e.g. 5d, 1mo, 1y)")
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


class IngestResponse(BaseModel):
    success: bool
    instrument: str
    source: str
    timeframe: str
    bars_ingested: int
    quality_score: float
    file_path: str
    checksum_sha256: str
    date_range_start: str
    date_range_end: str


class CandleItem(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    regime: Optional[str] = None


class CandleListResponse(BaseModel):
    instrument: str
    timeframe: str
    total_bars: int
    candles: List[CandleItem]


class StrategySummary(BaseModel):
    strategy_id: str
    name: str
    category: str
    version_tag: str
    description: str
    parameters: List[Dict[str, Any]]


class BacktestRunRequest(BaseModel):
    strategy_id: str = Field(default="gold_ema_trend_cross")
    instrument: str = Field(default="GC=F")
    timeframe: str = Field(default="1h")
    initial_capital: float = Field(default=10000.0, ge=100.0)
    risk_per_trade_pct: float = Field(default=1.0, ge=0.1, le=5.0)
    spread_usd: float = Field(default=0.30, ge=0.0)
    slippage_usd: float = Field(default=0.15, ge=0.0)
    parameters: Optional[Dict[str, Any]] = None


class BacktestRunResponse(BaseModel):
    backtest_id: str
    strategy_id: str
    instrument: str
    timeframe: str
    start_date: str
    end_date: str
    metrics: Dict[str, Any]
    total_trades: int
    execution_assumptions: Dict[str, Any]
    trades: List[Dict[str, Any]]
    equity_curve: List[Dict[str, Any]]
