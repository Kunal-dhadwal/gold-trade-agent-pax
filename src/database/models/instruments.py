"""Instrument database models."""

from sqlalchemy import Boolean, Float, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from src.database.models.base import Base, TimestampMixin


class Instrument(Base, TimestampMixin):
    __tablename__ = "instruments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(32), default="COMMODITY")
    quote_currency: Mapped[str] = mapped_column(String(10), default="USD")
    tick_size: Mapped[float] = mapped_column(Float, default=0.01)
    lot_size: Mapped[float] = mapped_column(Float, default=0.01)
    contract_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    maker_fee_rate: Mapped[float] = mapped_column(Float, default=0.0002)
    taker_fee_rate: Mapped[float] = mapped_column(Float, default=0.0005)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
