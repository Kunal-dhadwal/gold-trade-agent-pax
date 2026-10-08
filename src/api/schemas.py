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


# ==============================================================================
# Phase 3: Paper Trading & Risk Schemas
# ==============================================================================

class CreatePaperAccountIn(BaseModel):
    account_id: str = Field(..., description="Unique account identifier")
    initial_balance: float = Field(default=10000.0, ge=100.0, le=10000000.0)
    currency: str = Field(default="USDT")


class PaperAccountOut(BaseModel):
    account_id: str
    currency: str
    balance: float
    equity: float
    status: str
    created_at: str


class PaperAccountDetailOut(BaseModel):
    account_id: str
    currency: str
    balance: float
    equity: float
    status: str
    active_session_id: Optional[str] = None
    open_positions_count: int = 0
    total_trades_count: int = 0
    created_at: str


class PortfolioOut(BaseModel):
    account_id: str
    balance: float
    equity: float
    margin_used: float
    margin_free: float
    unrealized_pnl: float
    realized_pnl: float
    daily_pnl: float
    peak_equity: float
    drawdown_pct: float
    updated_at: str


class EquityPointOut(BaseModel):
    timestamp: str
    equity: float
    balance: float
    drawdown_pct: float


class PaperStrategyOut(BaseModel):
    strategy_id: str
    name: str
    category: str
    status: str
    active_session_id: Optional[str] = None


class StartPaperSessionIn(BaseModel):
    strategy_id: str
    account_id: str
    symbol: str = "XAUUSD"
    allocated_capital: float = 10000.0
    parameters: Optional[Dict[str, Any]] = None


class PaperSessionOut(BaseModel):
    session_id: str
    strategy_id: str
    account_id: str
    status: str
    started_at: str
    stopped_at: Optional[str] = None


class PaperOrderOut(BaseModel):
    order_id: str
    account_id: str
    strategy_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    status: str
    filled_price: Optional[float] = None
    filled_quantity: float = 0.0
    created_at: str


class PositionOut(BaseModel):
    position_id: str
    account_id: str
    strategy_id: str
    symbol: str
    side: str
    entry_price: float
    current_price: float
    quantity: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    unrealized_pnl: float
    realized_pnl: float
    status: str
    mae_usd: float = 0.0
    mfe_usd: float = 0.0
    opened_at: str
    closed_at: Optional[str] = None


class PaperTradeOut(BaseModel):
    trade_id: str
    account_id: str
    strategy_id: str
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    fee: float
    slippage: float
    entry_time: str
    exit_time: str


class RiskEventOut(BaseModel):
    event_id: str
    timestamp: str
    gate_name: str
    severity: str
    action: str
    details: str


class KillSwitchIn(BaseModel):
    operator_id: str
    reason: str
    policy: str = Field(default="FLATTEN_ALL", description="FLATTEN_ALL or CANCEL_ONLY")


class KillSwitchOut(BaseModel):
    status: str
    triggered_at: str
    operator_id: str
    policy: str
    positions_flattened: int
    orders_canceled: int
    message: str


class ParityDimensionOut(BaseModel):
    name: str
    dimension_index: int
    passed: bool
    status: str
    measured_value: float
    threshold_value: float
    unit: str
    details: str = ""


class ParityReportOut(BaseModel):
    strategy_id: str
    overall_status: str
    is_promotable: bool
    total_backtest_trades: int
    total_paper_trades: int
    slippage_drift_pct: float
    pnl_tracking_error_pct: float
    signal_congruence_pct: float
    trade_count_deviation_pct: float
    disqualification_reasons: List[str]
    dimensions: List[ParityDimensionOut]


class PromotionEvaluationOut(BaseModel):
    strategy_id: str
    stage1_passed: bool
    stage2_passed: bool
    eligible_for_live_candidate: bool
    evaluated_at: str
    operator_sign_off_status: str
    signed_by: Optional[str] = None
    signature: Optional[str] = None
    signed_at: Optional[str] = None
    disqualifications: List[str]
    gate_results: List[Dict[str, Any]]


class PromotionSignOffIn(BaseModel):
    operator_id: str
    operator_secret: str


class PromotionSignOffOut(BaseModel):
    status: str
    strategy_id: str
    signed_by: str
    signature: str
    signed_at: str
    new_state: str
    message: str

