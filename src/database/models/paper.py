"""SQLAlchemy database models for Phase 3 Paper Trading."""

from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PaperAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Authoritative durable record of a paper trading account."""

    __tablename__ = "paper_accounts"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    initial_balance: Mapped[float] = mapped_column(Float, nullable=False, default=10000.0)
    current_cash: Mapped[float] = mapped_column(Float, nullable=False, default=10000.0)
    peak_equity: Mapped[float] = mapped_column(Float, nullable=False, default=10000.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    currency: Mapped[str] = mapped_column(String(16), nullable=False, default="USDT")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")

    sessions: Mapped[List["PaperTradingSession"]] = relationship(
        "PaperTradingSession", back_populates="account", cascade="all, delete-orphan"
    )
    equity_snapshots: Mapped[List["PaperEquitySnapshot"]] = relationship(
        "PaperEquitySnapshot", back_populates="account", cascade="all, delete-orphan"
    )


class PaperTradingSession(Base, UUIDPrimaryKeyMixin):
    """Lifecycle session linking a strategy version to a paper account."""

    __tablename__ = "paper_trading_sessions"

    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("strategy_versions.id"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, default="PAXGUSDT")
    allocated_capital: Mapped[float] = mapped_column(Float, nullable=False, default=10000.0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PAPER_ACTIVE")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    total_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    winning_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    losing_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    gross_profit: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    gross_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    account: Mapped["PaperAccount"] = relationship("PaperAccount", back_populates="sessions")


class PaperOrderEvent(Base, UUIDPrimaryKeyMixin):
    """Immutable audit trail of order state transitions for idempotency and recovery."""

    __tablename__ = "paper_order_events"

    order_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    from_state: Mapped[str] = mapped_column(String(24), nullable=False)
    to_state: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )


class PaperEquitySnapshot(Base, UUIDPrimaryKeyMixin):
    """Time-series equity snapshots for charting and drawdown monitoring."""

    __tablename__ = "paper_equity_snapshots"

    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("paper_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    cash: Mapped[float] = mapped_column(Float, nullable=False)
    used_margin: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    account: Mapped["PaperAccount"] = relationship("PaperAccount", back_populates="equity_snapshots")

