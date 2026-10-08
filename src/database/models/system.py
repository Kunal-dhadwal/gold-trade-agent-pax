"""MarketRegimeRecord, AgentDecision, and SystemEventRecord models."""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from src.database.models.base import Base, UUIDPrimaryKeyMixin


class MarketRegimeRecord(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "market_regimes"

    instrument_id: Mapped[str] = mapped_column(String(64), ForeignKey("instruments.id"), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    regime_type: Mapped[str] = mapped_column(String(32), nullable=False)
    adx_value: Mapped[Optional[float]] = mapped_column(Float)
    atr_percent: Mapped[Optional[float]] = mapped_column(Float)
    bb_width: Mapped[Optional[float]] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, default=100.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AgentDecision(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "agent_decisions"

    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    input_context_summary: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    reasoning_output: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_version_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("strategy_versions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SystemEventRecord(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "system_events"

    component: Mapped[str] = mapped_column(String(64), nullable=False)
    event_name: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="SUCCESS")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
