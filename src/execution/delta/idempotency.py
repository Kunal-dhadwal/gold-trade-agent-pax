"""Idempotent Client Order ID generation and durable idempotency management.

Guarantees:
- Client order ID adheres to <= 32 alphanumeric format: AGY_{STRATEGY_ID[:6]}_{BAR_TIMESTAMP}_{NONCE}.
- IdempotencyManager prevents duplicate logical orders from being dispatched.
- Conflicting parameters under identical client_order_id raise IdempotencyConflictError.
"""

import asyncio
from datetime import datetime, timezone
import re
import secrets
from typing import Any, Dict, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.execution.delta.types import IdempotencyConflictError


class ClientOrderIdGenerator:
    """Deterministic and unique Client Order ID generator adhering to Delta Exchange constraints."""

    PATTERN = re.compile(r"^AGY_[A-Z0-9]{1,6}_[0-9]+_[A-Z0-9]+$")
    MAX_LENGTH = 32

    @classmethod
    def generate(
        cls,
        strategy_id: str,
        bar_timestamp: int | str,
        nonce: Optional[str] = None,
    ) -> str:
        """Generate a valid client order ID.
        
        Format: AGY_{STRATEGY_ID[:6]}_{BAR_TIMESTAMP}_{NONCE}
        Max length: 32 characters.
        """
        # Sanitize strategy ID: keep alphanumeric only, uppercase, at most 6 characters
        clean_strat = re.sub(r"[^A-Za-z0-9]", "", str(strategy_id)).upper()[:6]
        if not clean_strat:
            clean_strat = "DEF"

        # Sanitize timestamp: integer digits
        try:
            ts_str = str(int(bar_timestamp))
        except (ValueError, TypeError):
            # Fallback to current unix seconds if invalid
            ts_str = str(int(datetime.now(timezone.utc).timestamp()))

        # Generate or sanitize nonce
        if nonce is not None:
            clean_nonce = re.sub(r"[^A-Za-z0-9]", "", str(nonce)).upper()
            if not clean_nonce:
                clean_nonce = secrets.token_hex(2).upper()
        else:
            # 6-character hex nonce by default
            clean_nonce = secrets.token_hex(3).upper()

        # Combine prefix and fixed elements
        base = f"AGY_{clean_strat}_{ts_str}_"
        
        # Calculate available budget for nonce
        available_nonce_len = cls.MAX_LENGTH - len(base)
        if available_nonce_len < 1:
            # If timestamp was too long, clamp timestamp and nonce to fit exactly within 32 chars
            budget_for_ts = cls.MAX_LENGTH - len(f"AGY_{clean_strat}__") - 4  # Reserve 4 for nonce
            ts_str = ts_str[-budget_for_ts:]
            base = f"AGY_{clean_strat}_{ts_str}_"
            available_nonce_len = cls.MAX_LENGTH - len(base)

        clean_nonce = clean_nonce[:available_nonce_len]
        client_order_id = f"{base}{clean_nonce}"

        if not cls.validate(client_order_id):
            raise ValueError(f"Generated client order ID '{client_order_id}' does not satisfy SLA constraints")

        return client_order_id

    @classmethod
    def validate(cls, client_order_id: str) -> bool:
        """Validate client order ID against format and length constraints."""
        if not client_order_id or len(client_order_id) > cls.MAX_LENGTH:
            return False
        return bool(cls.PATTERN.match(client_order_id))


import json
import uuid
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker


class IdempotencyManager:
    """Manages order submission idempotency to eliminate duplicate logical orders across processes."""

    def __init__(self, session_factory: Optional[async_sessionmaker[AsyncSession]] = None):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self.session_factory = session_factory

    async def register_order(
        self,
        client_order_id: str,
        order_payload: Dict[str, Any],
        session: Optional[AsyncSession] = None,
        account_id: str = "ACC_LIVE_PRIMARY",
    ) -> Tuple[Dict[str, Any], bool]:
        """Register an order intent idempotently.
        
        Persists order intent into PostgreSQL before any exchange submission.
        Enforces cross-process uniqueness on client_order_id via database constraints.
        
        Returns:
            Tuple[Dict[str, Any], bool]: (order_record, is_duplicate)
            If is_duplicate is True, the order was previously registered with identical parameters.
            
        Raises:
            IdempotencyConflictError: If client_order_id already exists with conflicting parameters.
        """
        async with self._lock:
            # 1. Check in-memory cache first
            existing = self._cache.get(client_order_id)
            if existing is not None:
                self._validate_parameter_consistency(client_order_id, existing, order_payload)
                return existing, True

            # 2. Database persistence & cross-process uniqueness check
            if session is not None:
                return await self._register_in_session(client_order_id, order_payload, session, account_id)
            elif self.session_factory is not None:
                async with self.session_factory() as sess:
                    return await self._register_in_session(client_order_id, order_payload, sess, account_id)

            # 3. Memory-only fallback (used when no database session or factory is configured)
            record = dict(order_payload)
            record["client_order_id"] = client_order_id
            record["registered_at"] = datetime.now(timezone.utc).isoformat()
            record["state"] = record.get("state", "CREATED")
            self._cache[client_order_id] = record
            return record, False

    async def _register_in_session(
        self,
        client_order_id: str,
        order_payload: Dict[str, Any],
        session: AsyncSession,
        account_id: str,
    ) -> Tuple[Dict[str, Any], bool]:
        """Execute transactional registration within an active AsyncSession."""
        from src.database.models.live import LiveAccount, LiveOrder, LiveOrderEvent

        # 1. Check if order exists in DB
        stmt = select(LiveOrder).where(LiveOrder.client_order_id == client_order_id)
        result = await session.execute(stmt)
        db_order = result.scalar_one_or_none()

        if db_order is not None:
            existing = {
                "order_id": db_order.order_id,
                "client_order_id": db_order.client_order_id,
                "symbol": db_order.product_symbol,
                "size": db_order.quantity,
                "side": db_order.side,
                "order_type": db_order.order_type,
                "price": db_order.price,
                "stop_price": db_order.stop_price,
                "reduce_only": db_order.reduce_only,
                "state": db_order.state,
                "id": db_order.exchange_order_id,
            }
            self._validate_parameter_consistency(client_order_id, existing, order_payload)
            self._cache[client_order_id] = existing
            return existing, True

        # 2. Ensure account exists to satisfy foreign key constraint
        acc_stmt = select(LiveAccount).where(LiveAccount.account_id == account_id)
        acc_res = await session.execute(acc_stmt)
        if acc_res.scalar_one_or_none() is None:
            new_acc = LiveAccount(
                account_id=account_id,
                name="Primary Live Account",
                currency="USDT",
                initial_balance=10000.0,
                current_cash=10000.0,
                status="ACTIVE",
            )
            session.add(new_acc)
            await session.flush()

        # 3. Create durable order intent in CREATED state
        order_id = str(uuid.uuid4())
        price_val = None
        if "limit_price" in order_payload and order_payload["limit_price"] is not None:
            price_val = float(order_payload["limit_price"])
        elif "price" in order_payload and order_payload["price"] is not None:
            price_val = float(order_payload["price"])

        stop_price_val = None
        if "stop_price" in order_payload and order_payload["stop_price"] is not None:
            stop_price_val = float(order_payload["stop_price"])

        new_order = LiveOrder(
            order_id=order_id,
            account_id=account_id,
            client_order_id=client_order_id,
            product_symbol=str(order_payload.get("symbol", "PAXGUSDT")),
            side=str(order_payload.get("side", "")).upper(),
            order_type=str(order_payload.get("order_type", "")).upper(),
            time_in_force=str(order_payload.get("time_in_force", "GTC")).upper(),
            state="CREATED",
            quantity=float(order_payload.get("size", 0)),
            price=price_val,
            stop_price=stop_price_val,
            filled_quantity=0.0,
            reduce_only=bool(order_payload.get("reduce_only", False)),
        )

        event = LiveOrderEvent(
            id=str(uuid.uuid4()),
            order_id=order_id,
            from_state="CREATED",
            to_state="CREATED",
            event_type="REGISTER_INTENT",
            payload=json.dumps(order_payload),
            timestamp=datetime.now(timezone.utc),
        )

        try:
            session.add(new_order)
            session.add(event)
            await session.commit()
        except IntegrityError:
            # Concurrent insertion occurred with identical client_order_id
            await session.rollback()
            existing_db = None
            existing = None
            retry_stmt = select(LiveOrder).where(LiveOrder.client_order_id == client_order_id)
            for _ in range(10):
                await asyncio.sleep(0.05)
                if self.session_factory is not None:
                    async with self.session_factory() as read_session:
                        retry_res = await read_session.execute(retry_stmt)
                        existing_db = retry_res.scalar_one_or_none()
                        if existing_db is not None:
                            existing = {
                                "order_id": existing_db.order_id,
                                "client_order_id": existing_db.client_order_id,
                                "symbol": existing_db.product_symbol,
                                "size": existing_db.quantity,
                                "side": existing_db.side,
                                "order_type": existing_db.order_type,
                                "price": existing_db.price,
                                "stop_price": existing_db.stop_price,
                                "reduce_only": existing_db.reduce_only,
                                "state": existing_db.state,
                                "id": existing_db.exchange_order_id,
                            }
                            break
                else:
                    retry_res = await session.execute(retry_stmt)
                    existing_db = retry_res.scalar_one_or_none()
                    if existing_db is not None:
                        existing = {
                            "order_id": existing_db.order_id,
                            "client_order_id": existing_db.client_order_id,
                            "symbol": existing_db.product_symbol,
                            "size": existing_db.quantity,
                            "side": existing_db.side,
                            "order_type": existing_db.order_type,
                            "price": existing_db.price,
                            "stop_price": existing_db.stop_price,
                            "reduce_only": existing_db.reduce_only,
                            "state": existing_db.state,
                            "id": existing_db.exchange_order_id,
                        }
                        break

            if existing is not None:
                self._validate_parameter_consistency(client_order_id, existing, order_payload)
                self._cache[client_order_id] = existing
                return existing, True
            raise

        record = dict(order_payload)
        record["order_id"] = order_id
        record["client_order_id"] = client_order_id
        record["state"] = "CREATED"
        record["registered_at"] = datetime.now(timezone.utc).isoformat()
        self._cache[client_order_id] = record
        return record, False

    def _validate_parameter_consistency(
        self,
        client_order_id: str,
        existing: Dict[str, Any],
        requested: Dict[str, Any],
    ) -> None:
        """Validate parameter consistency against existing registered order."""
        cls_check_keys = ["symbol", "side", "size", "order_type", "reduce_only", "limit_price", "stop_price"]
        for k in cls_check_keys:
            if k in requested and k in existing and requested[k] is not None and existing[k] is not None:
                if k in ("size", "quantity"):
                    if abs(float(existing[k]) - float(requested[k])) > 1e-6:
                        raise IdempotencyConflictError(
                            f"Idempotency conflict for '{client_order_id}': parameter '{k}' mismatch "
                            f"(existing={existing[k]}, requested={requested[k]})"
                        )
                elif k in ("limit_price", "stop_price", "price"):
                    if abs(float(existing[k]) - float(requested[k])) > 1e-6:
                        raise IdempotencyConflictError(
                            f"Idempotency conflict for '{client_order_id}': parameter '{k}' mismatch "
                            f"(existing={existing[k]}, requested={requested[k]})"
                        )
                elif k == "reduce_only":
                    if bool(existing[k]) != bool(requested[k]):
                        raise IdempotencyConflictError(
                            f"Idempotency conflict for '{client_order_id}': parameter '{k}' mismatch "
                            f"(existing={existing[k]}, requested={requested[k]})"
                        )
                else:
                    expected_val = str(existing[k]).upper()
                    actual_val = str(requested[k]).upper()
                    if expected_val != actual_val:
                        raise IdempotencyConflictError(
                            f"Idempotency conflict for '{client_order_id}': parameter '{k}' mismatch "
                            f"(existing={existing[k]}, requested={requested[k]})"
                        )

    async def get_order(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve existing order record by client_order_id."""
        async with self._lock:
            existing = self._cache.get(client_order_id)
            if existing is not None:
                return existing

            if self.session_factory is not None:
                async with self.session_factory() as sess:
                    from src.database.models.live import LiveOrder
                    stmt = select(LiveOrder).where(LiveOrder.client_order_id == client_order_id)
                    res = await sess.execute(stmt)
                    db_order = res.scalar_one_or_none()
                    if db_order is not None:
                        record = {
                            "order_id": db_order.order_id,
                            "client_order_id": db_order.client_order_id,
                            "symbol": db_order.product_symbol,
                            "size": db_order.quantity,
                            "side": db_order.side,
                            "order_type": db_order.order_type,
                            "price": db_order.price,
                            "stop_price": db_order.stop_price,
                            "reduce_only": db_order.reduce_only,
                            "state": db_order.state,
                            "id": db_order.exchange_order_id,
                        }
                        self._cache[client_order_id] = record
                        return record
            return None

    async def update_state(
        self,
        client_order_id: str,
        new_state: str,
        exchange_order_id: Optional[str] = None,
        event_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Update tracked state of registered order in cache and PostgreSQL."""
        async with self._lock:
            if client_order_id in self._cache:
                self._cache[client_order_id]["state"] = new_state
                if exchange_order_id is not None:
                    self._cache[client_order_id]["id"] = exchange_order_id

            if self.session_factory is not None:
                async with self.session_factory() as sess:
                    from src.database.models.live import LiveOrder, LiveOrderEvent
                    stmt = select(LiveOrder).where(LiveOrder.client_order_id == client_order_id)
                    res = await sess.execute(stmt)
                    db_order = res.scalar_one_or_none()
                    if db_order is not None:
                        old_state = db_order.state
                        db_order.state = new_state
                        if exchange_order_id is not None:
                            db_order.exchange_order_id = str(exchange_order_id)

                        ev = LiveOrderEvent(
                            id=str(uuid.uuid4()),
                            order_id=db_order.order_id,
                            from_state=old_state,
                            to_state=new_state,
                            event_type=f"TRANSITION_TO_{new_state}",
                            payload=json.dumps(event_payload or {}),
                            timestamp=datetime.now(timezone.utc),
                        )
                        sess.add(ev)
                        await sess.commit()


