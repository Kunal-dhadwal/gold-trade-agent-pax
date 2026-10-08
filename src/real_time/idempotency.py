"""Two-tier event deduplication and idempotency engine."""

import logging
from typing import Any, Optional, Set
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.database.models.paper import PaperOrderEvent

logger = logging.getLogger(__name__)


class IdempotencyEngine:
    """Provides two-tier event deduplication (Redis Fast Check + PostgreSQL Uniqueness)."""

    def __init__(self, redis_client: Optional[Any] = None, ttl_seconds: int = 86400):
        self.redis = redis_client
        self.ttl = ttl_seconds
        self._local_keys: Set[str] = set()

    @staticmethod
    def build_signal_key(strategy_version_id: str, candle_timestamp_iso: str, direction: str) -> str:
        """Deterministic key for strategy signals."""
        return f"SIG:{strategy_version_id}:{candle_timestamp_iso}:{direction.upper()}"

    @staticmethod
    def build_order_key(account_id: str, signal_id: str, order_type: str) -> str:
        """Deterministic key for paper orders."""
        return f"ORD:{account_id}:{signal_id}:{order_type.upper()}"

    @staticmethod
    def build_fill_key(order_id: str, fill_timestamp_iso: str) -> str:
        """Deterministic key for virtual fills."""
        return f"FILL:{order_id}:{fill_timestamp_iso}"

    async def is_duplicate(self, idempotency_key: str, db_session: Optional[AsyncSession] = None) -> bool:
        """Check if an event key has already been processed.

        Returns True if duplicate (should be dropped), False if new (should be processed).
        """
        # Tier 1: Local / Redis Fast Check
        if self.redis:
            try:
                # SET key 1 NX EX ttl -> returns True if key was set (new), None/False if existed
                redis_key = f"idempotency:event:{idempotency_key}"
                is_new = await self.redis.set(redis_key, "1", nx=True, ex=self.ttl)
                if not is_new:
                    logger.debug(f"Tier 1 (Redis) flagged duplicate event: {idempotency_key}")
                    return True
            except Exception as e:
                logger.warning(f"Redis idempotency check failed: {e}. Falling back to memory/DB.")

        if idempotency_key in self._local_keys:
            logger.debug(f"Tier 1 (Memory) flagged duplicate event: {idempotency_key}")
            return True

        # Tier 2: PostgreSQL Authoritative Check (if session provided)
        if db_session:
            stmt = select(PaperOrderEvent.id).where(PaperOrderEvent.idempotency_key == idempotency_key).limit(1)
            result = await db_session.execute(stmt)
            if result.scalar_one_or_none() is not None:
                logger.info(f"Tier 2 (PostgreSQL) flagged duplicate event: {idempotency_key}")
                self._local_keys.add(idempotency_key)
                return True

        # Mark as seen locally
        self._local_keys.add(idempotency_key)
        return False

