"""SQLAlchemy models for Phase 2 research sessions and hypotheses."""

from typing import List, Optional
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ResearchSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Container for multi-agent strategy generation and validation sessions."""
    __tablename__ = "research_sessions"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    target_regime: Mapped[str] = mapped_column(String(32), default="ALL")
    timeframe: Mapped[str] = mapped_column(String(16), default="1h")
    instrument: Mapped[str] = mapped_column(String(32), default="XAUUSD")
    iterations_count: Mapped[int] = mapped_column(Integer, default=0)
    max_iterations: Mapped[int] = mapped_column(Integer, default=10)

    hypotheses: Mapped[List["ResearchHypothesis"]] = relationship(
        "ResearchHypothesis", back_populates="session", cascade="all, delete-orphan"
    )


class ResearchHypothesis(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Pre-registered falsifiable trading hypothesis."""
    __tablename__ = "research_hypotheses"

    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("research_sessions.id"), nullable=False)
    thesis_statement: Mapped[str] = mapped_column(Text, nullable=False)
    economic_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    expected_edge: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="FORMULATED")

    session: Mapped["ResearchSession"] = relationship("ResearchSession", back_populates="hypotheses")
    strategy_versions: Mapped[List["StrategyVersion"]] = relationship(
        "StrategyVersion", back_populates="hypothesis"
    )

