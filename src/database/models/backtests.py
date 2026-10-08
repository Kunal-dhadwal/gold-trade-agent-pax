"""Backtest and BacktestTrade models."""

from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database.models.base import Base, UUIDPrimaryKeyMixin


class Backtest(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "backtests"

    strategy_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("strategy_versions.id"), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(64), ForeignKey("instruments.id"), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    initial_capital: Mapped[float] = mapped_column(Float, default=10000.0)
    final_equity: Mapped[float] = mapped_column(Float, nullable=False)
    total_net_profit: Mapped[float] = mapped_column(Float, nullable=False)
    total_return_pct: Mapped[float] = mapped_column(Float, nullable=False)
    cagr_pct: Mapped[Optional[float]] = mapped_column(Float)
    sharpe_ratio: Mapped[Optional[float]] = mapped_column(Float)
    sortino_ratio: Mapped[Optional[float]] = mapped_column(Float)
    profit_factor: Mapped[Optional[float]] = mapped_column(Float)
    win_rate_pct: Mapped[Optional[float]] = mapped_column(Float)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False)
    max_drawdown_usd: Mapped[float] = mapped_column(Float, nullable=False)
    recovery_factor: Mapped[Optional[float]] = mapped_column(Float)
    expectancy_usd: Mapped[Optional[float]] = mapped_column(Float)
    average_r: Mapped[Optional[float]] = mapped_column(Float)
    total_trades: Mapped[int] = mapped_column(Integer, nullable=False)
    winning_trades: Mapped[int] = mapped_column(Integer, nullable=False)
    losing_trades: Mapped[int] = mapped_column(Integer, nullable=False)
    max_consecutive_losses: Mapped[Optional[int]] = mapped_column(Integer)
    max_adverse_excursion_avg: Mapped[Optional[float]] = mapped_column(Float)
    max_favorable_excursion_avg: Mapped[Optional[float]] = mapped_column(Float)
    total_fees_paid: Mapped[float] = mapped_column(Float, default=0.0)
    total_slippage_cost: Mapped[float] = mapped_column(Float, default=0.0)
    execution_assumptions: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    trades: Mapped[List["BacktestTrade"]] = relationship("BacktestTrade", back_populates="backtest", cascade="all, delete-orphan")


class BacktestTrade(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "backtest_trades"

    backtest_id: Mapped[str] = mapped_column(String(36), ForeignKey("backtests.id"), nullable=False)
    trade_number: Mapped[int] = mapped_column(Integer, nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    size: Mapped[float] = mapped_column(Float, nullable=False)
    initial_stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    initial_take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    gross_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    net_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    return_pct: Mapped[float] = mapped_column(Float, nullable=False)
    r_multiple: Mapped[float] = mapped_column(Float, nullable=False)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    slippage: Mapped[float] = mapped_column(Float, default=0.0)
    mae_pct: Mapped[Optional[float]] = mapped_column(Float)
    mfe_pct: Mapped[Optional[float]] = mapped_column(Float)
    exit_reason: Mapped[str] = mapped_column(String(32), nullable=False)

    backtest: Mapped["Backtest"] = relationship("Backtest", back_populates="trades")
