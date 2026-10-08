"""Unit and parity tests for database models, foreign keys, and type persistence."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import select
from src.database.connection import get_engine_args
from src.database.models.base import Base
from src.database.models.instruments import Instrument
from src.database.models.strategies import Strategy, StrategyVersion
from src.database.models.backtests import Backtest, BacktestTrade


@asynccontextmanager
async def create_test_db():
    """Create an isolated in-memory SQLite database with foreign keys enabled."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", **get_engine_args("sqlite+aiosqlite:///:memory:"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_foreign_key_enforcement():
    """Verify that inserting a trade with invalid backtest_id fails with IntegrityError."""
    async with create_test_db() as async_db:
        async with async_db() as session:
            orphaned_trade = BacktestTrade(
                backtest_id="non-existent-backtest-id",
                trade_number=1,
                direction="LONG",
                entry_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
                exit_time=datetime(2024, 1, 1, 13, 0, tzinfo=timezone.utc),
                entry_price=2000.0,
                exit_price=2020.0,
                size=1.0,
                initial_stop_loss=1980.0,
                initial_take_profit=2040.0,
                gross_pnl=20.0,
                net_pnl=18.0,
                return_pct=0.9,
                r_multiple=1.0,
                fees=1.0,
                slippage=1.0,
                exit_reason="TAKE_PROFIT",
            )
            session.add(orphaned_trade)
            with pytest.raises(IntegrityError):
                await session.commit()


@pytest.mark.asyncio
async def test_database_relational_integrity_and_json_persistence():
    """Verify valid relational persistence, cascade deletion, JSON round-tripping, and UTC datetimes."""
    async with create_test_db() as async_db:
        async with async_db() as session:
            # 1. Seed instrument
            inst = Instrument(
                id="XAUUSD",
                symbol="XAUUSD",
                name="Gold Spot",
                asset_class="COMMODITY",
                quote_currency="USD",
                tick_size=0.01,
                lot_size=0.01,
                contract_multiplier=1.0,
                maker_fee_rate=0.0002,
                taker_fee_rate=0.0005,
                is_active=True,
            )
            session.add(inst)

            # 2. Seed strategy and version
            strat = Strategy(
                id="strat_test",
                name="Test Strategy",
                category="TREND_FOLLOWING",
                description="Test description",
            )
            session.add(strat)

            version = StrategyVersion(
                id="strat_test_v1",
                strategy_id="strat_test",
                version_tag="1.0.0",
                timeframe="1h",
                logic_description="EMA cross logic",
            )
            session.add(version)
            await session.commit()

            # 3. Create Backtest with JSON execution assumptions
            assumptions = {
                "slippage_model": "linear_adverse",
                "slippage_bps": 2.0,
                "maker_fee": 0.0002,
                "execution_on_next_bar_open": True,
            }
            start_dt = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
            end_dt = datetime(2024, 1, 10, 0, 0, tzinfo=timezone.utc)

            backtest = Backtest(
                id="bt_test_001",
                strategy_version_id="strat_test_v1",
                instrument_id="XAUUSD",
                timeframe="1h",
                start_date=start_dt,
                end_date=end_dt,
                initial_capital=10000.0,
                final_equity=10500.0,
                total_net_profit=500.0,
                total_return_pct=5.0,
                max_drawdown_pct=1.5,
                max_drawdown_usd=150.0,
                total_trades=1,
                winning_trades=1,
                losing_trades=0,
                execution_assumptions=assumptions,
            )
            session.add(backtest)

            trade = BacktestTrade(
                id="trade_001",
                backtest_id="bt_test_001",
                trade_number=1,
                direction="LONG",
                entry_time=start_dt,
                exit_time=end_dt,
                entry_price=2000.0,
                exit_price=2050.0,
                size=1.0,
                initial_stop_loss=1980.0,
                initial_take_profit=2050.0,
                gross_pnl=50.0,
                net_pnl=48.0,
                return_pct=2.4,
                r_multiple=2.5,
                fees=1.0,
                slippage=1.0,
                exit_reason="TAKE_PROFIT",
            )
            session.add(trade)
            await session.commit()

            # 4. Query backtest and verify relationships, JSON, and datetimes
            result = await session.execute(select(Backtest).where(Backtest.id == "bt_test_001"))
            saved_bt = result.scalar_one()

            assert saved_bt.instrument_id == "XAUUSD"
            assert saved_bt.execution_assumptions["execution_on_next_bar_open"] is True
            assert saved_bt.execution_assumptions["slippage_bps"] == 2.0
            assert saved_bt.start_date.year == 2024
            assert saved_bt.created_at is not None

            # 5. Test cascade delete
            await session.delete(saved_bt)
            await session.commit()

            # Child trade must be cascade deleted
            trade_res = await session.execute(select(BacktestTrade).where(BacktestTrade.id == "trade_001"))
            assert trade_res.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_instrument_foreign_key_restricts_deletion():
    """Verify that deleting an instrument that is referenced by a backtest fails."""
    async with create_test_db() as async_db:
        async with async_db() as session:
            inst = Instrument(
                id="XAUUSD",
                symbol="XAUUSD",
                name="Gold Spot",
                asset_class="COMMODITY",
                quote_currency="USD",
                tick_size=0.01,
                lot_size=0.01,
                contract_multiplier=1.0,
                maker_fee_rate=0.0002,
                taker_fee_rate=0.0005,
                is_active=True,
            )
            session.add(inst)

            strat = Strategy(id="s1", name="S1", category="TREND")
            session.add(strat)

            ver = StrategyVersion(
                id="v1",
                strategy_id="s1",
                version_tag="1.0",
                timeframe="1h",
                logic_description="desc",
            )
            session.add_all([inst, strat, ver])
            await session.flush()

            bt = Backtest(
                id="bt1",
                strategy_version_id="v1",
                instrument_id="XAUUSD",
                timeframe="1h",
                start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
                end_date=datetime(2024, 1, 2, tzinfo=timezone.utc),
                initial_capital=10000.0,
                final_equity=10100.0,
                total_net_profit=100.0,
                total_return_pct=1.0,
                max_drawdown_pct=0.5,
                max_drawdown_usd=50.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
            )
            session.add(bt)
            await session.commit()

            # Attempt to delete instrument referenced by backtest
            await session.delete(inst)
            with pytest.raises(IntegrityError):
                await session.commit()


@pytest.mark.asyncio
async def test_init_db_seeding():
    """Verify init_db seeds canonical instruments into database."""
    from src.database.connection import init_db, AsyncSessionLocal

    await init_db()

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Instrument))
        instruments = result.scalars().all()
        inst_ids = [i.id for i in instruments]
        assert "XAUUSD" in inst_ids
        assert "GC=F" in inst_ids
        assert "XAUUSD_PERP" in inst_ids


