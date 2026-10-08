"""Signals, PaperTrades, LiveTrades, Positions, and RiskEvents models."""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database.models.base import Base, UUIDPrimaryKeyMixin


class Signal(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "signals"

    strategy_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("strategy_versions.id"), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(64), ForeignKey("instruments.id"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    suggested_entry: Mapped[float] = mapped_column(Float, nullable=False)
    suggested_sl: Mapped[float] = mapped_column(Float, nullable=False)
    suggested_tp: Mapped[float] = mapped_column(Float, nullable=False)
    suggested_size: Mapped[float] = mapped_column(Float, nullable=False)
    risk_reward_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    regime_context: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PaperTrade(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "paper_trades"

    signal_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("signals.id"))
    strategy_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("strategy_versions.id"), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(64), ForeignKey("instruments.id"), nullable=False)
    exchange_order_id: Mapped[Optional[str]] = mapped_column(String(128))
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), default="MARKET")
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[Optional[float]] = mapped_column(Float)
    size: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    realized_pnl: Mapped[Optional[float]] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    slippage: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    exit_reason: Mapped[Optional[str]] = mapped_column(String(32))
    mae_pct: Mapped[Optional[float]] = mapped_column(Float)
    mfe_pct: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class LiveTrade(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "live_trades"

    signal_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("signals.id"))
    strategy_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("strategy_versions.id"), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(64), ForeignKey("instruments.id"), nullable=False)
    exchange_order_id: Mapped[Optional[str]] = mapped_column(String(128))
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), default="MARKET")
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[Optional[float]] = mapped_column(Float)
    size: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    realized_pnl: Mapped[Optional[float]] = mapped_column(Float)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    slippage: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    exit_reason: Mapped[Optional[str]] = mapped_column(String(32))
    mae_pct: Mapped[Optional[float]] = mapped_column(Float)
    mfe_pct: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class Position(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "positions"

    instrument_id: Mapped[str] = mapped_column(String(64), ForeignKey("instruments.id"), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default="PAPER")
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    average_entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    current_mark_price: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    liquidation_price: Mapped[Optional[float]] = mapped_column(Float)
    allocated_margin: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class RiskEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "risk_events"

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="WARNING")
    signal_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("signals.id"))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_equity: Mapped[float] = mapped_column(Float, nullable=False)
    snapshot_drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False)
    action_taken: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
