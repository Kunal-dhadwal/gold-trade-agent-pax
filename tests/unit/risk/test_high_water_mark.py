"""Unit tests for HighWaterMarkManager, durable persistence, fencing epochs, and Gate 1 drawdown."""

from contextlib import asynccontextmanager
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.database.connection import get_engine_args
from src.database.models.base import Base
from src.database.models.live import LiveAccount, AccountEquityHighWaterMark
from src.risk.high_water_mark import (
    HighWaterMarkManager,
    HighWaterMarkError,
    StaleFencingEpochError,
)


@asynccontextmanager
async def create_test_db():
    """Create an isolated in-memory SQLite database with schema for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", **get_engine_args("sqlite+aiosqlite:///:memory:"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_high_water_mark_initialization_and_load():
    """Verify loading from absent row creates initial record, and subsequent load retrieves it."""
    async with create_test_db() as session_factory:
        # Seed parent live account
        async with session_factory() as session:
            account = LiveAccount(
                account_id="ACC_LIVE_TEST_01",
                name="Test Live Account",
                initial_balance=10000.0,
                current_cash=10000.0,
            )
            session.add(account)
            await session.commit()

        manager = HighWaterMarkManager(session_factory=session_factory)

        # 1. Initialize absent record
        peak = await manager.load_or_initialize(
            account_id="ACC_LIVE_TEST_01",
            current_equity=10000.0,
            fencing_epoch=1,
        )
        assert peak == 10000.0
        assert manager.get_cached_peak("ACC_LIVE_TEST_01") == 10000.0
        assert manager.get_cached_epoch("ACC_LIVE_TEST_01") == 1

        # 2. Re-loading retrieves existing record without overwriting
        reloaded = await manager.load_or_initialize(
            account_id="ACC_LIVE_TEST_01",
            current_equity=9500.0,
            fencing_epoch=2,
        )
        assert reloaded == 10000.0


@pytest.mark.asyncio
async def test_high_water_mark_negative_equity_rejected():
    """Verify that non-positive initial equity raises ValueError."""
    async with create_test_db() as session_factory:
        manager = HighWaterMarkManager(session_factory=session_factory)
        with pytest.raises(ValueError):
            await manager.load_or_initialize("ACC_LIVE_TEST_01", current_equity=-100.0)
        with pytest.raises(ValueError):
            await manager.load_or_initialize("ACC_LIVE_TEST_01", current_equity=0.0)


@pytest.mark.asyncio
async def test_high_water_mark_atomic_updates():
    """Verify peak equity update logic and fencing epoch checks."""
    async with create_test_db() as session_factory:
        async with session_factory() as session:
            account = LiveAccount(
                account_id="ACC_LIVE_TEST_02",
                name="Test Live Account 2",
                initial_balance=10000.0,
                current_cash=10000.0,
            )
            session.add(account)
            await session.commit()

        manager = HighWaterMarkManager(session_factory=session_factory)
        await manager.load_or_initialize("ACC_LIVE_TEST_02", current_equity=10000.0, fencing_epoch=1)

        # Equity lower than peak -> no update
        peak, updated = await manager.update_peak("ACC_LIVE_TEST_02", current_equity=9800.0, fencing_epoch=1)
        assert peak == 10000.0
        assert updated is False

        # Equity equal to peak -> no update
        peak, updated = await manager.update_peak("ACC_LIVE_TEST_02", current_equity=10000.0, fencing_epoch=1)
        assert peak == 10000.0
        assert updated is False

        # Equity higher than peak -> updates peak
        peak, updated = await manager.update_peak("ACC_LIVE_TEST_02", current_equity=10500.0, fencing_epoch=2)
        assert peak == 10500.0
        assert updated is True
        assert manager.get_cached_peak("ACC_LIVE_TEST_02") == 10500.0
        assert manager.get_cached_epoch("ACC_LIVE_TEST_02") == 2


@pytest.mark.asyncio
async def test_high_water_mark_stale_fencing_epoch_rejection():
    """Verify that an update call carrying a stale fencing epoch is strictly rejected."""
    async with create_test_db() as session_factory:
        async with session_factory() as session:
            account = LiveAccount(
                account_id="ACC_LIVE_TEST_03",
                name="Test Live Account 3",
                initial_balance=10000.0,
                current_cash=10000.0,
            )
            session.add(account)
            await session.commit()

        manager = HighWaterMarkManager(session_factory=session_factory)
        await manager.load_or_initialize("ACC_LIVE_TEST_03", current_equity=10000.0, fencing_epoch=5)

        # Calling update with epoch 4 (< persisted epoch 5) with higher equity must raise StaleFencingEpochError
        with pytest.raises(StaleFencingEpochError) as exc_info:
            await manager.update_peak("ACC_LIVE_TEST_03", current_equity=11000.0, fencing_epoch=4)
        assert "Fencing epoch rejection" in str(exc_info.value)


@pytest.mark.asyncio
async def test_high_water_mark_persistence_across_restart():
    """Verify that a newly instantiated manager retrieves persisted high-water mark across simulated restarts."""
    async with create_test_db() as session_factory:
        async with session_factory() as session:
            account = LiveAccount(
                account_id="ACC_LIVE_RESTART",
                name="Restart Test Account",
                initial_balance=10000.0,
                current_cash=10000.0,
            )
            session.add(account)
            await session.commit()

        # Session 1: Manager boots, establishes peak at 10000, updates to 12000 with epoch 3
        manager_1 = HighWaterMarkManager(session_factory=session_factory)
        await manager_1.load_or_initialize("ACC_LIVE_RESTART", current_equity=10000.0, fencing_epoch=1)
        await manager_1.update_peak("ACC_LIVE_RESTART", current_equity=12000.0, fencing_epoch=3)

        # Session 2: Simulated process restart (new manager instance with empty memory cache)
        manager_2 = HighWaterMarkManager(session_factory=session_factory)
        assert manager_2.get_cached_peak("ACC_LIVE_RESTART") is None

        # Re-initialize on reboot with current equity $11,500
        loaded_peak = await manager_2.load_or_initialize("ACC_LIVE_RESTART", current_equity=11500.0, fencing_epoch=4)
        assert loaded_peak == 12000.0
        assert manager_2.get_cached_peak("ACC_LIVE_RESTART") == 12000.0
        assert manager_2.get_cached_epoch("ACC_LIVE_RESTART") == 3


@pytest.mark.asyncio
async def test_drawdown_calculation_and_gate_1_ratified_6_pct_veto():
    """Verify Gate 1 portfolio peak-to-trough drawdown calculation and 6.0% threshold enforcement."""
    async with create_test_db() as session_factory:
        async with session_factory() as session:
            account = LiveAccount(
                account_id="ACC_LIVE_DRAWDOWN",
                name="Drawdown Account",
                initial_balance=10000.0,
                current_cash=10000.0,
            )
            session.add(account)
            await session.commit()

        manager = HighWaterMarkManager(session_factory=session_factory)
        await manager.load_or_initialize("ACC_LIVE_DRAWDOWN", current_equity=10000.0)

        # 1. Zero drawdown when equity == peak
        assert manager.calculate_drawdown_pct("ACC_LIVE_DRAWDOWN", 10000.0) == 0.0
        assert manager.is_gate1_breached("ACC_LIVE_DRAWDOWN", 10000.0, threshold_pct=6.0) is False

        # 2. 3.0% drawdown at $9,700
        dd_3 = manager.calculate_drawdown_pct("ACC_LIVE_DRAWDOWN", 9700.0)
        assert dd_3 == 3.0
        assert manager.is_gate1_breached("ACC_LIVE_DRAWDOWN", 9700.0, threshold_pct=6.0) is False

        # 3. 5.99% drawdown at $9,401 -> Gate 1 NOT breached
        dd_599 = manager.calculate_drawdown_pct("ACC_LIVE_DRAWDOWN", 9401.0)
        assert dd_599 == 5.99
        assert manager.is_gate1_breached("ACC_LIVE_DRAWDOWN", 9401.0, threshold_pct=6.0) is False

        # 4. Exactly 6.0% drawdown at $9,400 -> Gate 1 BREACHED (owner ratified threshold)
        dd_60 = manager.calculate_drawdown_pct("ACC_LIVE_DRAWDOWN", 9400.0)
        assert dd_60 == 6.0
        assert manager.is_gate1_breached("ACC_LIVE_DRAWDOWN", 9400.0, threshold_pct=6.0) is True

        # 5. 6.01% drawdown at $9,399 -> Gate 1 BREACHED
        dd_601 = manager.calculate_drawdown_pct("ACC_LIVE_DRAWDOWN", 9399.0)
        assert dd_601 == 6.01
        assert manager.is_gate1_breached("ACC_LIVE_DRAWDOWN", 9399.0, threshold_pct=6.0) is True


@pytest.mark.asyncio
async def test_update_uninitialized_account_raises_error():
    """Verify that updating an uninitialized account raises HighWaterMarkError."""
    async with create_test_db() as session_factory:
        manager = HighWaterMarkManager(session_factory=session_factory)
        with pytest.raises(HighWaterMarkError):
            await manager.update_peak("UNINITIALIZED_ACC", current_equity=10000.0, fencing_epoch=1)
