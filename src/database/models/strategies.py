"""Strategy, StrategyVersion, and StrategyParameter models."""

from typing import List, Optional
from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Strategy(Base, TimestampMixin):
    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(64), default="QUANT_ENGINEER")

    versions: Mapped[List["StrategyVersion"]] = relationship("StrategyVersion", back_populates="strategy", cascade="all, delete-orphan")


class StrategyVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "strategy_versions"

    strategy_id: Mapped[str] = mapped_column(String(64), ForeignKey("strategies.id"), nullable=False)
    version_tag: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="RESEARCH")
    logic_description: Mapped[str] = mapped_column(Text, nullable=False)
    entry_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    exit_rules: Mapped[dict] = mapped_column(JSON, default=dict)
    stop_loss_logic: Mapped[dict] = mapped_column(JSON, default=dict)
    take_profit_logic: Mapped[dict] = mapped_column(JSON, default=dict)
    position_sizing_logic: Mapped[dict] = mapped_column(JSON, default=dict)
    pine_script_code: Mapped[Optional[str]] = mapped_column(Text)

    strategy: Mapped["Strategy"] = relationship("Strategy", back_populates="versions")
    parameters: Mapped[List["StrategyParameter"]] = relationship("StrategyParameter", back_populates="version", cascade="all, delete-orphan")


class StrategyParameter(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "strategy_parameters"

    strategy_version_id: Mapped[str] = mapped_column(String(36), ForeignKey("strategy_versions.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    param_type: Mapped[str] = mapped_column(String(32), default="FLOAT")
    default_value: Mapped[str] = mapped_column(String(64), nullable=False)
    min_value: Mapped[Optional[str]] = mapped_column(String(64))
    max_value: Mapped[Optional[str]] = mapped_column(String(64))
    step: Mapped[Optional[str]] = mapped_column(String(64))
    optimized_value: Mapped[Optional[str]] = mapped_column(String(64))

    version: Mapped["StrategyVersion"] = relationship("StrategyVersion", back_populates="parameters")
