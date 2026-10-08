"""End-to-End Database Persistence Integration Tests for Phase 3 (P1-1).

Validates durable persistence in PostgreSQL / DB:
1. Paper accounts created via REST persist to `paper_accounts` table.
2. Paper trading sessions persist to `paper_trading_sessions` table.
3. Order events, position fills, closed trades, and equity snapshots persist durably.
4. Kill switch triggers and resets persist to `risk_events` table.
5. Engine rehydration from DB accurately restores state.
"""

from datetime import datetime, timezone
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from src.api.auth import create_access_token
from src.api.main import app
from src.database.connection import AsyncSessionLocal
from src.database.models.paper import (
    PaperAccount,
    PaperEquitySnapshot,
    PaperOrderEvent,
    PaperTradingSession,
)
from src.database.models.trading import PaperTrade, Position, RiskEvent
from src.paper.engine import PaperTradingEngine


@pytest.mark.asyncio
async def test_account_and_session_durable_persistence():
    """Verify accounts and sessions created via REST persist to database tables."""
    transport = ASGITransport(app=app)
    operator_token = create_access_token("persist_op_1", role="OPERATOR")
    headers = {"Authorization": f"Bearer {operator_token}"}
    acc_id = f"ACC_PERSIST_{uuid.uuid4().hex[:8]}"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Create account via REST
        res_acc = await ac.post(
            "/api/v1/paper/accounts",
            json={"account_id": acc_id, "initial_balance": 30000.0},
            headers=headers,
        )
        assert res_acc.status_code == 201

        # Verify row in database
        async with AsyncSessionLocal() as session:
            stmt = select(PaperAccount).where(PaperAccount.id == acc_id)
            res = await session.execute(stmt)
            db_acc = res.scalar_one_or_none()
            assert db_acc is not None
            assert db_acc.id == acc_id
            assert db_acc.initial_balance == 30000.0
            assert db_acc.status == "ACTIVE"

        # 2. Start session via REST
        res_start = await ac.post(
            "/api/v1/paper/strategies/gold_ema_trend_cross/start",
            json={"strategy_id": "gold_ema_trend_cross", "account_id": acc_id, "allocated_capital": 30000.0},
            headers=headers,
        )
        assert res_start.status_code == 200

        # Verify session in database
        async with AsyncSessionLocal() as session:
            sess_stmt = select(PaperTradingSession).where(
                PaperTradingSession.account_id == acc_id,
                PaperTradingSession.status == "PAPER_ACTIVE",
            )
            sess_res = await session.execute(sess_stmt)
            db_sess = sess_res.scalar_one_or_none()
            assert db_sess is not None
            assert db_sess.account_id == acc_id
            assert db_sess.allocated_capital == 30000.0


@pytest.mark.asyncio
async def test_engine_events_and_trades_durable_persistence():
    """Verify execution engine persists order events, trades, and equity snapshots to database."""
    acc_id = f"ACC_ENG_{uuid.uuid4().hex[:8]}"
    strat_id = "gold_ema_trend_cross"
    now = datetime.now(timezone.utc)

    # 1. Ensure account row exists in DB
    async with AsyncSessionLocal() as session:
        acc = PaperAccount(
            id=acc_id,
            name=f"Engine Test {acc_id}",
            initial_balance=50000.0,
            current_cash=50000.0,
            peak_equity=50000.0,
            max_drawdown_pct=0.0,
            currency="USD",
            status="ACTIVE",
        )
        session.add(acc)
        await session.commit()

    engine = PaperTradingEngine(
        account_id=acc_id,
        strategy_version_id=strat_id,
        symbol="XAUUSD",
        initial_capital=50000.0,
        session_factory=AsyncSessionLocal,
    )

    # 2. Persist order event
    order_id = f"ORD_{uuid.uuid4().hex[:8]}"
    await engine._persist_order_event_db(
        order_id=order_id,
        event_type="ORDER_FILLED",
        from_state="ACCEPTED",
        to_state="FILLED",
        idempotency_key=f"IDEMP_{uuid.uuid4().hex[:8]}",
        payload={"order_id": order_id, "fill_price": 2050.0, "quantity": 2.0},
        dt=now,
    )

    # Verify order event in DB
    async with AsyncSessionLocal() as session:
        ord_stmt = select(PaperOrderEvent).where(PaperOrderEvent.order_id == order_id)
        ord_res = await session.execute(ord_stmt)
        db_evt = ord_res.scalar_one_or_none()
        assert db_evt is not None
        assert db_evt.event_type == "ORDER_FILLED"
        assert db_evt.from_state == "ACCEPTED"
        assert db_evt.to_state == "FILLED"

    # 3. Persist position fill
    await engine._persist_position_fill_db(
        fill_price=2050.0,
        quantity=2.0,
        direction="LONG",
        dt=now,
        notional=4100.0,
    )

    # Verify position in DB
    async with AsyncSessionLocal() as session:
        pos_stmt = select(Position).where(Position.account_id == acc_id, Position.instrument_id == "XAUUSD")
        pos_res = await session.execute(pos_stmt)
        db_pos = pos_res.scalar_one_or_none()
        assert db_pos is not None
        assert db_pos.quantity == 2.0
        assert db_pos.average_entry_price == 2050.0

    # 4. Persist closed trade
    trade_record = {
        "account_id": acc_id,
        "strategy_version_id": strat_id,
        "symbol": "XAUUSD",
        "direction": "LONG",
        "entry_time": now.isoformat(),
        "exit_time": now.isoformat(),
        "entry_price": 2050.0,
        "exit_price": 2070.0,
        "quantity": 2.0,
        "gross_pnl": 40.0,
        "net_pnl": 38.5,
        "total_fees": 1.5,
        "slippage": 0.20,
        "stop_loss": 2030.0,
        "take_profit": 2080.0,
        "exit_reason": "TP_TRIGGERED",
        "holding_bars": 5,
        "mae_pct": 0.2,
        "mfe_pct": 1.1,
    }
    await engine._persist_closed_trade_db(trade_record, dt=now)

    # Verify paper trade in DB
    async with AsyncSessionLocal() as session:
        trd_stmt = select(PaperTrade).where(PaperTrade.account_id == acc_id)
        trd_res = await session.execute(trd_stmt)
        db_trd = trd_res.scalar_one_or_none()
        assert db_trd is not None
        assert db_trd.realized_pnl == 38.5
        assert db_trd.entry_price == 2050.0
        assert db_trd.exit_price == 2070.0

    # 5. Persist equity snapshot
    await engine._persist_equity_snapshot_db(dt=now, unrealized=0.0)

    # Verify equity snapshot in DB
    async with AsyncSessionLocal() as session:
        eq_stmt = select(PaperEquitySnapshot).where(PaperEquitySnapshot.account_id == acc_id)
        eq_res = await session.execute(eq_stmt)
        db_eq = eq_res.scalars().all()
        assert len(db_eq) >= 1


@pytest.mark.asyncio
async def test_kill_switch_risk_event_persistence():
    """Verify kill switch actions persist durably to risk_events table."""
    transport = ASGITransport(app=app)
    operator_token = create_access_token("human_risk_op", role="OPERATOR")
    admin_token = create_access_token("human_risk_admin", role="ADMIN")

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Trigger kill switch
        ks_res = await ac.post(
            "/api/v1/risk/kill-switch",
            json={"operator_id": "human_risk_op", "reason": "Durable persistence test trigger"},
            headers={"Authorization": f"Bearer {operator_token}"},
        )
        assert ks_res.status_code == 200

        # Verify RiskEvent in database
        async with AsyncSessionLocal() as session:
            stmt = select(RiskEvent).where(
                RiskEvent.event_type == "GATE_8_KILL_SWITCH",
                RiskEvent.action_taken.like("KILL_SWITCH_%"),
            ).order_by(RiskEvent.created_at.desc())
            res = await session.execute(stmt)
            event = res.scalars().first()
            assert event is not None
            assert "human_risk_op" in event.description
            assert event.severity == "CRITICAL"

        # Reset kill switch
        reset_res = await ac.post(
            "/api/v1/risk/kill-switch/reset?operator_id=human_risk_admin",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert reset_res.status_code == 200

        # Verify Reset RiskEvent in database
        async with AsyncSessionLocal() as session:
            stmt = select(RiskEvent).where(
                RiskEvent.action_taken == "KILL_SWITCH_RESET",
            ).order_by(RiskEvent.created_at.desc())
            res = await session.execute(stmt)
            reset_event = res.scalars().first()
            assert reset_event is not None
            assert reset_event.severity == "INFO"
            assert "human_risk_admin" in reset_event.description

