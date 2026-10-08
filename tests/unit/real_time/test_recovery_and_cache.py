"""Unit tests for StateRehydrationEngine and StateCache recovery."""

import uuid
import pytest
from sqlalchemy import delete
from src.database.connection import AsyncSessionLocal
from src.database.models.paper import PaperAccount
from src.database.models.trading import Position
from src.real_time.recovery import StateRehydrationEngine
from src.real_time.state_cache import StateCache


@pytest.mark.asyncio
async def test_rehydration_engine_fallback():
    cache = StateCache()
    engine = StateRehydrationEngine(state_cache=cache)
    result = await engine.rehydrate_all()

    assert "status" in result
    assert result["status"] == "REHYDRATED"
    assert "duration_ms" in result
    assert result["duration_ms"] < 2000.0  # <2s SLA verification


@pytest.mark.asyncio
async def test_rehydration_from_db_with_seeded_data():
    """P2-3: Benchmark real PostgreSQL/database state rehydration under data volume respecting <2s SLA."""
    cache = StateCache()
    engine = StateRehydrationEngine(state_cache=cache)

    async with AsyncSessionLocal() as session:
        seed_acc_ids = [f"ACC_REHYD_{i}_{uuid.uuid4().hex[:6]}" for i in range(10)]
        for acc_id in seed_acc_ids:
            acc = PaperAccount(
                id=acc_id,
                name=f"Rehydration Test {acc_id}",
                initial_balance=10000.0,
                current_cash=10000.0,
                peak_equity=10000.0,
                max_drawdown_pct=0.0,
                currency="USD",
                status="ACTIVE",
            )
            session.add(acc)
        await session.flush()

        for i, acc_id in enumerate(seed_acc_ids):
            pos = Position(
                instrument_id="XAUUSD",
                account_id=acc_id,
                mode="PAPER",
                direction="LONG" if i % 2 == 0 else "SHORT",
                quantity=1.0 + i,
                average_entry_price=2000.0,
                current_mark_price=2005.0,
                unrealized_pnl=5.0,
                allocated_margin=100.0,
            )
            session.add(pos)
        await session.commit()

        try:
            res = await engine.rehydrate_from_db(session)
            assert res["status"] == "SUCCESS"
            assert res["sla_passed"] is True
            assert res["duration_ms"] <= 2000.0
            assert res["accounts_rehydrated"] >= 10
            assert res["positions_rehydrated"] >= 10

            acc_0 = await cache.get_account(seed_acc_ids[0])
            assert acc_0 is not None
            assert acc_0["id"] == seed_acc_ids[0]
            assert acc_0["initial_balance"] == 10000.0

            pos_0 = await cache.get_position(seed_acc_ids[0], "XAUUSD")
            assert pos_0 is not None
            assert pos_0["account_id"] == seed_acc_ids[0]
            assert pos_0["quantity"] == 1.0
        finally:
            del_pos_stmt = delete(Position).where(Position.account_id.in_(seed_acc_ids))
            await session.execute(del_pos_stmt)
            del_acc_stmt = delete(PaperAccount).where(PaperAccount.id.in_(seed_acc_ids))
            await session.execute(del_acc_stmt)
            await session.commit()


@pytest.mark.asyncio
async def test_state_cache_risk_state():
    cache = StateCache()
    await cache.set_risk_state("ACC_1", {"kill_switch": "NORMAL", "active": False})
    status = await cache.get_risk_state("ACC_1")
    assert status is not None
    assert status["kill_switch"] == "NORMAL"
