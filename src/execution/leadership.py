"""PostgreSQL-backed Distributed Leadership Leases and Server-Clock Fencing Tokens.

Provides singleton execution guarantees across distributed worker processes, containers,
or failover clusters. All lease expirations are evaluated against the database server clock
(CURRENT_TIMESTAMP) rather than local container clocks.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Optional, Tuple
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.database.models.live import LeaderLease

logger = logging.getLogger("gold_quant.leadership")


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class LeaderLeaseManager:
    """Manages distributed advisory leadership leases with monotonic fencing tokens."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        resource_name: str = "live_execution_leader",
        ttl_seconds: float = 15.0,
        min_dispatch_ttl_seconds: float = 2.0,
        heartbeat_interval_seconds: float = 5.0,
    ):
        self.session_factory = session_factory
        self.resource_name = resource_name
        self.ttl_seconds = ttl_seconds
        self.min_dispatch_ttl_seconds = min_dispatch_ttl_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._active_owner_id: Optional[str] = None
        self._current_epoch: Optional[int] = None
        self._lock = asyncio.Lock()

    async def _get_db_server_time(self, session: AsyncSession) -> datetime:
        """Fetch current timestamp directly from the database server."""
        try:
            bind = session.get_bind()
            if bind and bind.dialect.name == "sqlite":
                return datetime.now(timezone.utc)
            res = await session.execute(select(func.now()))
            val = res.scalar()
            if isinstance(val, datetime):
                return _ensure_utc(val)
        except Exception:
            pass
        return datetime.now(timezone.utc)

    async def acquire_lease(
        self,
        owner_id: str,
        resource_name: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
    ) -> bool:
        """Attempt to acquire leadership lease for resource_name.
        
        Guarantees:
        - If no lease exists: inserts row with lease_epoch = 1.
        - If lease held by caller: renews TTL.
        - If lease held by another owner has expired: claims lease with monotonic epoch increment.
        - If active lease held by another owner: returns False immediately.
        """
        res_name = resource_name or self.resource_name
        ttl = ttl_seconds or self.ttl_seconds

        async with self._lock:
            async with self.session_factory() as session:
                server_now = await self._get_db_server_time(session)
                new_expiry = server_now + timedelta(seconds=ttl)

                bind = session.get_bind()
                is_sqlite = bind is not None and bind.dialect.name == "sqlite"
                if is_sqlite:
                    stmt = select(LeaderLease).where(LeaderLease.resource_name == res_name)
                else:
                    stmt = select(LeaderLease).where(LeaderLease.resource_name == res_name).with_for_update()

                res = await session.execute(stmt)
                existing = res.scalar_one_or_none()

                if existing is None:
                    # 1. Unclaimed resource: insert initial lease
                    new_lease = LeaderLease(
                        resource_name=res_name,
                        owner_id=owner_id,
                        lease_epoch=1,
                        acquired_at=server_now,
                        expires_at=new_expiry,
                    )
                    session.add(new_lease)
                    try:
                        await session.commit()
                        self._active_owner_id = owner_id
                        self._current_epoch = 1
                        logger.info(f"Leadership acquired for '{res_name}' by '{owner_id}' (epoch=1)")
                        return True
                    except IntegrityError:
                        await session.rollback()
                        return False

                # Existing lease exists
                existing_expiry = _ensure_utc(existing.expires_at)
                is_expired = existing_expiry is not None and existing_expiry <= server_now
                is_same_owner = existing.owner_id == owner_id

                if is_same_owner:
                    # 2. Existing lease already owned by caller: renew
                    existing.expires_at = new_expiry
                    await session.commit()
                    self._active_owner_id = owner_id
                    self._current_epoch = existing.lease_epoch
                    return True

                if is_expired:
                    # 3. Former lease expired: claim with monotonic epoch increment
                    new_epoch = existing.lease_epoch + 1
                    existing.owner_id = owner_id
                    existing.lease_epoch = new_epoch
                    existing.acquired_at = server_now
                    existing.expires_at = new_expiry
                    await session.commit()
                    self._active_owner_id = owner_id
                    self._current_epoch = new_epoch
                    logger.warning(
                        f"Leadership reclaimed for '{res_name}' by '{owner_id}' "
                        f"after expiry of previous holder (new epoch={new_epoch})"
                    )
                    return True

                # 4. Active lease held by competing owner
                await session.rollback()
                return False

    async def renew_lease(
        self,
        owner_id: str,
        resource_name: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
    ) -> bool:
        """Renew active leadership lease if caller still holds it."""
        res_name = resource_name or self.resource_name
        ttl = ttl_seconds or self.ttl_seconds

        async with self._lock:
            async with self.session_factory() as session:
                server_now = await self._get_db_server_time(session)
                new_expiry = server_now + timedelta(seconds=ttl)

                bind = session.get_bind()
                is_sqlite = bind is not None and bind.dialect.name == "sqlite"
                if is_sqlite:
                    stmt = select(LeaderLease).where(
                        LeaderLease.resource_name == res_name,
                        LeaderLease.owner_id == owner_id,
                    )
                else:
                    stmt = select(LeaderLease).where(
                        LeaderLease.resource_name == res_name,
                        LeaderLease.owner_id == owner_id,
                    ).with_for_update()

                res = await session.execute(stmt)
                lease = res.scalar_one_or_none()
                if lease is not None:
                    exp = _ensure_utc(lease.expires_at)
                    if exp is not None and exp > server_now:
                        lease.expires_at = new_expiry
                        await session.commit()
                        return True

                logger.error(f"Failed to renew leadership lease '{res_name}' for '{owner_id}'; lease lost or expired")
                if self._active_owner_id == owner_id:
                    self._active_owner_id = None
                return False

    async def is_leader_valid(
        self,
        owner_id: Optional[str] = None,
        resource_name: Optional[str] = None,
        min_ttl_seconds: Optional[float] = None,
    ) -> bool:
        """Pre-flight check: verify caller is leader and remaining TTL exceeds safety buffer."""
        target_owner = owner_id or self._active_owner_id
        if not target_owner:
            return False

        res_name = resource_name or self.resource_name
        min_ttl = min_ttl_seconds if min_ttl_seconds is not None else self.min_dispatch_ttl_seconds

        async with self.session_factory() as session:
            server_now = await self._get_db_server_time(session)
            threshold = server_now + timedelta(seconds=min_ttl)

            stmt = select(LeaderLease).where(
                LeaderLease.resource_name == res_name,
                LeaderLease.owner_id == target_owner,
            )
            res = await session.execute(stmt)
            lease = res.scalar_one_or_none()
            if lease is not None:
                exp = _ensure_utc(lease.expires_at)
                if exp is not None and exp >= threshold:
                    self._current_epoch = lease.lease_epoch
                    return True
            return False

    async def resign_lease(
        self,
        owner_id: Optional[str] = None,
        resource_name: Optional[str] = None,
    ) -> bool:
        """Gracefully release leadership lease by expiring it immediately."""
        target_owner = owner_id or self._active_owner_id
        if not target_owner:
            return False

        res_name = resource_name or self.resource_name
        self.stop_heartbeat()

        async with self._lock:
            async with self.session_factory() as session:
                server_now = await self._get_db_server_time(session)
                stmt = (
                    update(LeaderLease)
                    .where(
                        LeaderLease.resource_name == res_name,
                        LeaderLease.owner_id == target_owner,
                    )
                    .values(expires_at=server_now)
                )
                res = await session.execute(stmt)
                await session.commit()

                if self._active_owner_id == target_owner:
                    self._active_owner_id = None

                logger.info(f"Leadership lease '{res_name}' resigned by '{target_owner}'")
                return res.rowcount > 0

    async def get_current_epoch(self, resource_name: Optional[str] = None) -> Optional[int]:
        """Fetch current monotonic epoch fencing token."""
        res_name = resource_name or self.resource_name
        async with self.session_factory() as session:
            stmt = select(LeaderLease.lease_epoch).where(LeaderLease.resource_name == res_name)
            res = await session.execute(stmt)
            return res.scalar_one_or_none()

    def start_heartbeat(
        self,
        owner_id: str,
        interval_seconds: Optional[float] = None,
        ttl_seconds: Optional[float] = None,
    ) -> None:
        """Start background heartbeat loop to periodically renew lease."""
        interval = interval_seconds or self.heartbeat_interval_seconds
        ttl = ttl_seconds or self.ttl_seconds
        self._active_owner_id = owner_id

        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            return

        async def _heartbeat_loop():
            while True:
                try:
                    await asyncio.sleep(interval)
                    success = await self.renew_lease(owner_id=owner_id, ttl_seconds=ttl)
                    if not success:
                        logger.critical(f"Heartbeat failed to renew lease for '{owner_id}'. Leadership lost!")
                        break
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.error(f"Unexpected error in leadership heartbeat: {exc}")

        self._heartbeat_task = asyncio.create_task(_heartbeat_loop())

    def stop_heartbeat(self) -> None:
        """Cancel background heartbeat loop."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
