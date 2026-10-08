"""Validation, WalkForward, and MonteCarlo result models."""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from src.database.models.base import Base, UUIDPrimaryKeyMixin


class ValidationResult(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "validation_results"

    backtest_id: Mapped[str] = mapped_column(String(36), ForeignKey("backtests.id"), nullable=False)
    oos_start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    oos_end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    oos_sharpe: Mapped[float] = mapped_column(Float, nullable=False)
    oos_profit_factor: Mapped[float] = mapped_column(Float, nullable=False)
    oos_drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False)
    degradation_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    is_oos_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    robustness_score: Mapped[float] = mapped_column(Float, default=0.0)
    rejection_reasons: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class WalkForwardResult(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "walk_forward_results"

    strategy_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("strategy_versions.id"), nullable=False)
    windows_count: Mapped[int] = mapped_column(Integer, nullable=False)
    train_window_bars: Mapped[int] = mapped_column(Integer, nullable=False)
    test_window_bars: Mapped[int] = mapped_column(Integer, nullable=False)
    walk_forward_efficiency: Mapped[float] = mapped_column(Float, nullable=False)
    is_consistent: Mapped[bool] = mapped_column(Boolean, default=False)
    window_details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class MonteCarloResult(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "monte_carlo_results"

    backtest_id: Mapped[str] = mapped_column(String(36), ForeignKey("backtests.id"), nullable=False)
    iterations: Mapped[int] = mapped_column(Integer, default=5000)
    median_drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False)
    p95_drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False)
    worst_case_drawdown_pct: Mapped[float] = mapped_column(Float, nullable=False)
    probability_of_ruin: Mapped[float] = mapped_column(Float, nullable=False)
    p95_losing_streak: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence_intervals: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
