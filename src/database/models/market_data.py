"""Market data metadata database model."""

from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.database.models.base import Base, UUIDPrimaryKeyMixin


class MarketDataMetadata(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "market_data_metadata"

    instrument_id: Mapped[str] = mapped_column(String(64), ForeignKey("instruments.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    candle_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, default=100.0)
    is_normalized: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    instrument = relationship("Instrument")
