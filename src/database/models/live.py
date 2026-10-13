"""SQLAlchemy database models for Phase 4 Milestone 4.2 Live Execution entities."""

from datetime import datetime, timezone
from typing import Optional
import uuid
from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database.models.base import Base, TimestampMixin


class LiveAccount(Base, TimestampMixin):
    """Authoritative durable record of a live trading account."""

    __tablename__ = "live_accounts"

    account_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(16), default="USDT", nullable=False)
    initial_balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    current_cash: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False)

    fee_schedules: Mapped[list["AccountFeeSchedule"]] = relationship(
        "AccountFeeSchedule", back_populates="account", cascade="all, delete-orphan"
    )
    orders: Mapped[list["LiveOrder"]] = relationship(
        "LiveOrder", back_populates="account", cascade="all, delete-orphan"
    )


class AccountFeeSchedule(Base, TimestampMixin):
    """Account fee tier configuration with maker rebate support."""

    __tablename__ = "account_fee_schedules"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("live_accounts.account_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_symbol: Mapped[str] = mapped_column(String(32), default="PAXGUSDT", nullable=False)
    tier_level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    maker_rate: Mapped[float] = mapped_column(Float, default=0.0005, nullable=False)
    taker_rate: Mapped[float] = mapped_column(Float, default=0.0010, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="REST_AUTHENTICATED_TIER_1", nullable=False)

    account: Mapped["LiveAccount"] = relationship("LiveAccount", back_populates="fee_schedules")


class LiveOrder(Base, TimestampMixin):
    """Live order entity managed by OrderLifecycleFSM."""

    __tablename__ = "live_orders"

    order_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("live_accounts.account_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    client_order_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    exchange_order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    product_symbol: Mapped[str] = mapped_column(String(32), default="PAXGUSDT", nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # BUY / SELL
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)  # LIMIT / MARKET / STOP_MARKET
    time_in_force: Mapped[str] = mapped_column(String(10), default="GTC", nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="CREATED", nullable=False, index=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    filled_quantity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    average_fill_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reduce_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    account: Mapped["LiveAccount"] = relationship("LiveAccount", back_populates="orders")
    events: Mapped[list["LiveOrderEvent"]] = relationship(
        "LiveOrderEvent", back_populates="order", cascade="all, delete-orphan"
    )


class LiveOrderEvent(Base):
    """Audit log of order state machine transitions."""

    __tablename__ = "live_order_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    order_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("live_orders.order_id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_state: Mapped[str] = mapped_column(String(24), nullable=False)
    to_state: Mapped[str] = mapped_column(String(24), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )

    order: Mapped["LiveOrder"] = relationship("LiveOrder", back_populates="events")


class LeaderLease(Base):
    """Advisory leadership lease with monotonic epoch token."""

    __tablename__ = "leader_leases"

    resource_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_epoch: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), default=1, nullable=False
    )
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class ReconciliationSnapshot(Base):
    """Three-way continuous reconciliation log entry."""

    __tablename__ = "reconciliation_snapshots"

    snapshot_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("live_accounts.account_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )
    discrepancy_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cash_local: Mapped[float] = mapped_column(Float, nullable=False)
    cash_exchange: Mapped[float] = mapped_column(Float, nullable=False)
    upnl_local: Mapped[float] = mapped_column(Float, nullable=False)
    upnl_exchange: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="CLEAN", nullable=False)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class LiveFundingEvent(Base):
    """Perpetual funding fee settlement cash movements."""

    __tablename__ = "live_funding_events"

    event_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("live_accounts.account_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    product_symbol: Mapped[str] = mapped_column(String(32), default="PAXGUSDT", nullable=False)
    funding_rate: Mapped[float] = mapped_column(Float, nullable=False)
    payment_amount: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )


class OperatorReconciliationAuditLog(Base):
    """Audit log of operator manual intervention and adoption actions."""

    __tablename__ = "operator_reconciliation_audit_logs"

    log_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    snapshot_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("reconciliation_snapshots.snapshot_id", ondelete="SET NULL"), nullable=True
    )
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    operator_id: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )


class ConsumedAuthorizationNonce(Base):
    """Durable consumed nonce table for replay attack prevention."""

    __tablename__ = "consumed_authorization_nonces"

    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    signer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    consumed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )


class AccountEquityHighWaterMark(Base):
    """Persisted high-water mark for Gate 1 portfolio peak-to-trough drawdown calculation."""

    __tablename__ = "account_equity_high_water_mark"

    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("live_accounts.account_id", ondelete="RESTRICT"), primary_key=True
    )
    peak_equity: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    fencing_epoch: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), default=1, nullable=False
    )
