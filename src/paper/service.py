"""Paper Trading Service Registry and Lifecycle Coordinator.

Provides access to active PaperTradingEngine instances across REST and WebSocket APIs.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.database.models.paper import PaperAccount, PaperTradingSession
from src.database.models.trading import Position, RiskEvent
from src.paper.engine import PaperTradingEngine
from src.paper.positions import PositionTracker
from src.real_time.event_bus import EventBus
from src.real_time.idempotency import IdempotencyEngine
from src.real_time.recovery import StateRehydrationEngine
from src.real_time.state_cache import StateCache

logger = logging.getLogger(__name__)


class PaperTradingService:
    """Singleton-style coordinator for PaperTradingEngine instances backed by durable PostgreSQL store."""

    _instance: Optional["PaperTradingService"] = None

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        state_cache: Optional[StateCache] = None,
        idempotency: Optional[IdempotencyEngine] = None,
    ):
        self.event_bus = event_bus or EventBus()
        self.state_cache = state_cache or StateCache()
        self.idempotency = idempotency or IdempotencyEngine()
        self._engines: Dict[str, PaperTradingEngine] = {}
        self._accounts: Dict[str, Dict] = {}
        self._risk_events: List[Dict] = []

    @classmethod
    def get_instance(cls) -> "PaperTradingService":
        if cls._instance is None:
            cls._instance = PaperTradingService()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for clean testing isolation."""
        cls._instance = None

    def get_engine(self, account_id: str) -> Optional[PaperTradingEngine]:
        return self._engines.get(account_id)

    def get_all_engines(self) -> List[PaperTradingEngine]:
        return list(self._engines.values())

    def get_or_create_engine(
        self,
        account_id: str,
        strategy_version_id: str = "gold_ema_trend_cross",
        initial_capital: float = settings.PAPER_DEFAULT_INITIAL_CAPITAL,
        symbol: str = settings.DELTA_TARGET_SYMBOL,
    ) -> PaperTradingEngine:
        """Retrieve existing engine or instantiate a new one."""
        if account_id in self._engines:
            return self._engines[account_id]

        engine = PaperTradingEngine(
            account_id=account_id,
            strategy_version_id=strategy_version_id,
            symbol=symbol,
            initial_capital=initial_capital,
            event_bus=self.event_bus,
            state_cache=self.state_cache,
            idempotency=self.idempotency,
        )
        self._engines[account_id] = engine

        if account_id not in self._accounts:
            self._accounts[account_id] = {
                "account_id": account_id,
                "currency": "USDT",
                "initial_balance": initial_capital,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "ACTIVE",
            }

        return engine

    async def ensure_account_persisted(
        self,
        db: AsyncSession,
        account_id: str,
        initial_balance: float = settings.PAPER_DEFAULT_INITIAL_CAPITAL,
        currency: str = "USDT",
        name: Optional[str] = None,
    ) -> PaperAccount:
        """Ensure account exists in durable PostgreSQL database and memory engine."""
        stmt = select(PaperAccount).where(PaperAccount.id == account_id)
        res = await db.execute(stmt)
        acc = res.scalar_one_or_none()

        if not acc:
            acc = PaperAccount(
                id=account_id,
                name=name or account_id,
                initial_balance=initial_balance,
                current_cash=initial_balance,
                peak_equity=initial_balance,
                max_drawdown_pct=0.0,
                currency=currency,
                status="ACTIVE",
            )
            db.add(acc)
            await db.commit()
            await db.refresh(acc)

        # Sync in-memory engine
        eng = self.get_or_create_engine(
            account_id=acc.id,
            initial_capital=acc.initial_balance,
        )
        eng.portfolio.cash_balance = acc.current_cash
        eng.portfolio.peak_equity = acc.peak_equity

        self._accounts[acc.id] = {
            "account_id": acc.id,
            "currency": acc.currency,
            "initial_balance": acc.initial_balance,
            "current_cash": acc.current_cash,
            "peak_equity": acc.peak_equity,
            "status": acc.status,
            "created_at": acc.created_at.isoformat() if hasattr(acc, "created_at") and acc.created_at else datetime.now(timezone.utc).isoformat(),
        }

        return acc

    async def rehydrate_from_db(self, db: AsyncSession) -> Dict[str, Any]:
        """Restore in-memory engines and Redis state cache from authoritative PostgreSQL store."""
        logger.info("Executing runtime state rehydration from durable PostgreSQL store...")
        rehydration_engine = StateRehydrationEngine(state_cache=self.state_cache)
        cache_summary = await rehydration_engine.rehydrate_from_db(db)

        # 1. Rehydrate Paper Accounts into in-memory engines
        account_stmt = select(PaperAccount).where(PaperAccount.status != "CLOSED")
        accounts_res = await db.execute(account_stmt)
        accounts = accounts_res.scalars().all()

        for acc in accounts:
            eng = self.get_or_create_engine(
                account_id=acc.id,
                initial_capital=acc.initial_balance,
            )
            eng.portfolio.cash_balance = acc.current_cash
            eng.portfolio.peak_equity = acc.peak_equity
            eng.portfolio.initial_capital = acc.initial_balance

            self._accounts[acc.id] = {
                "account_id": acc.id,
                "currency": acc.currency,
                "initial_balance": acc.initial_balance,
                "current_cash": acc.current_cash,
                "peak_equity": acc.peak_equity,
                "status": acc.status,
                "created_at": acc.created_at.isoformat() if hasattr(acc, "created_at") and acc.created_at else datetime.now(timezone.utc).isoformat(),
            }

        # 2. Rehydrate Open Positions
        pos_stmt = select(Position).where(Position.mode == "PAPER", Position.quantity > 0)
        positions_res = await db.execute(pos_stmt)
        positions = positions_res.scalars().all()

        for pos in positions:
            if pos.account_id and pos.account_id in self._engines:
                eng = self._engines[pos.account_id]
                eng.position = PositionTracker(
                    symbol=pos.instrument_id,
                    direction=pos.direction,
                    quantity=pos.quantity,
                    entry_price=pos.average_entry_price,
                    stop_loss=pos.average_entry_price * 0.99,
                    take_profit=pos.average_entry_price * 1.02,
                    allocated_margin=pos.allocated_margin,
                    entry_fee=0.0,
                    entry_slippage=0.0,
                    opened_at=pos.updated_at or datetime.now(timezone.utc),
                )
                eng.position.current_mark_price = pos.current_mark_price

        # 3. Rehydrate recent Risk Events
        risk_stmt = select(RiskEvent).order_by(RiskEvent.created_at.desc()).limit(100)
        risk_res = await db.execute(risk_stmt)
        events = risk_res.scalars().all()
        for rev in reversed(events):
            self.record_risk_event({
                "event_id": rev.id,
                "event_type": rev.event_type,
                "severity": rev.severity,
                "description": rev.description,
                "action_taken": rev.action_taken,
                "snapshot_equity": rev.snapshot_equity,
                "snapshot_drawdown_pct": rev.snapshot_drawdown_pct,
                "timestamp": rev.created_at.isoformat(),
            })

        return {
            "status": "REHYDRATION_COMPLETE",
            "cache_summary": cache_summary,
            "engines_active": len(self._engines),
            "accounts_loaded": len(accounts),
            "positions_loaded": len(positions),
        }

    def record_risk_event(self, event: Dict) -> None:
        self._risk_events.append(event)
        if len(self._risk_events) > 1000:
            self._risk_events.pop(0)

    async def record_risk_event_durable(
        self,
        db: Optional[AsyncSession],
        event_type: str,
        severity: str,
        description: str,
        action_taken: str,
        snapshot_equity: float = 0.0,
        snapshot_drawdown_pct: float = 0.0,
        signal_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record risk event both in-memory and in durable PostgreSQL database."""
        event_dict = {
            "event_type": event_type,
            "severity": severity,
            "description": description,
            "action_taken": action_taken,
            "snapshot_equity": snapshot_equity,
            "snapshot_drawdown_pct": snapshot_drawdown_pct,
            "signal_id": signal_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.record_risk_event(event_dict)

        if db is not None:
            db_event = RiskEvent(
                event_type=event_type,
                severity=severity,
                description=description,
                action_taken=action_taken,
                snapshot_equity=snapshot_equity,
                snapshot_drawdown_pct=snapshot_drawdown_pct,
                signal_id=signal_id,
            )
            db.add(db_event)
            await db.commit()
            await db.refresh(db_event)
            event_dict["event_id"] = db_event.id

        return event_dict

    def get_risk_events(self, severity: Optional[str] = None) -> List[Dict]:
        if severity:
            sev_upper = severity.upper()
            return [e for e in self._risk_events if e.get("severity", "").upper() == sev_upper]
        return list(self._risk_events)


paper_service = PaperTradingService.get_instance()


