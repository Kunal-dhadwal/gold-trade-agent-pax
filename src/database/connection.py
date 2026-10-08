"""Database engine and asynchronous session factory."""

from typing import AsyncGenerator
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from src.core.config import settings
from src.core.logger import logger
from src.database.models.base import Base
from src.database.models import Instrument


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable SQLite foreign key constraints."""
    module = type(dbapi_connection).__module__.lower()
    conn_type = type(dbapi_connection).__name__.lower()
    if "sqlite" in module or "sqlite" in conn_type:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_engine_args(url: str):
    args = {"echo": False}
    if url.startswith("sqlite"):
        args["connect_args"] = {"check_same_thread": False}
    else:
        args["pool_size"] = settings.DATABASE_POOL_SIZE
        args["max_overflow"] = settings.DATABASE_MAX_OVERFLOW
    return args


engine = create_async_engine(settings.DATABASE_URL, **get_engine_args(settings.DATABASE_URL))

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing database session to FastAPI routes."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database tables and seed default instruments."""
    logger.info(f"Initializing database tables with URL: {settings.DATABASE_URL.split('@')[-1]}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed default Gold instruments if not present
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        result = await session.execute(select(Instrument).where(Instrument.id == "XAUUSD"))
        if not result.scalar_one_or_none():
            xau_spot = Instrument(
                id="XAUUSD",
                symbol="XAUUSD",
                name="Gold Spot / US Dollar",
                asset_class="COMMODITY",
                quote_currency="USD",
                tick_size=0.01,
                lot_size=0.01,
                contract_multiplier=1.0,
                maker_fee_rate=0.0002,
                taker_fee_rate=0.0005,
                is_active=True,
            )
            gc_futures = Instrument(
                id="GC=F",
                symbol="GC=F",
                name="Gold Comex Continuous Futures",
                asset_class="COMMODITY",
                quote_currency="USD",
                tick_size=0.10,
                lot_size=1.0,
                contract_multiplier=100.0,
                maker_fee_rate=0.0002,
                taker_fee_rate=0.0005,
                is_active=True,
            )
            delta_gold = Instrument(
                id="XAUUSD_PERP",
                symbol="XAUUSD",
                name="Delta Exchange Gold Perpetual",
                asset_class="CRYPTO_DERIVATIVE",
                quote_currency="USD",
                tick_size=0.01,
                lot_size=0.001,
                contract_multiplier=1.0,
                maker_fee_rate=0.0002,
                taker_fee_rate=0.0005,
                is_active=True,
            )
            paxg_delta = Instrument(
                id="PAXGUSDT",
                symbol="PAXGUSDT",
                name="PAX Gold Tether Perpetual (Delta Exchange)",
                asset_class="CRYPTO_DERIVATIVE",
                quote_currency="USDT",
                tick_size=0.01,
                lot_size=0.001,
                contract_multiplier=0.001,
                maker_fee_rate=0.0002,
                taker_fee_rate=0.0002,
                is_active=True,
            )
            session.add_all([xau_spot, gc_futures, delta_gold, paxg_delta])
            await session.commit()
            logger.info("Seeded canonical Gold instruments (XAUUSD, GC=F, XAUUSD_PERP).")
