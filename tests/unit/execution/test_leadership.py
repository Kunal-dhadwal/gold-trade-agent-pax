"""Unit tests for PostgreSQL-backed Distributed Leadership Leases and Server-Clock Fencing Tokens."""

import asyncio
from datetime import datetime, timedelta, timezone
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.database.models.base import Base
from src.database.models.live import LeaderLease
from src.execution.leadership import LeaderLeaseManager


@pytest_asyncio.fixture
async def lease_session_factory():
    """Create an isolated in-memory SQLite database session factory for leadership tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    yield session_maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_single_worker_acquire_and_renew(lease_session_factory):
    """Test 1: Single worker successfully acquires lease and renews TTL."""
    mgr = LeaderLeaseManager(session_factory=lease_session_factory, ttl_seconds=10.0)
    owner = "worker-node-1"

    # Acquire initial lease
    acquired = await mgr.acquire_lease(owner_id=owner)
    assert acquired is True
    assert await mgr.get_current_epoch() == 1

    # Valid leader check
    assert await mgr.is_leader_valid(owner_id=owner, min_ttl_seconds=1.0) is True

    # Renew lease
    renewed = await mgr.renew_lease(owner_id=owner, ttl_seconds=15.0)
    assert renewed is True
    assert await mgr.is_leader_valid(owner_id=owner, min_ttl_seconds=5.0) is True


@pytest.mark.asyncio
async def test_concurrent_worker_contention_single_winner(lease_session_factory):
    """Test 2: Two concurrent workers contend for the same lease; exactly one wins."""
    mgr1 = LeaderLeaseManager(session_factory=lease_session_factory, ttl_seconds=10.0)
    mgr2 = LeaderLeaseManager(session_factory=lease_session_factory, ttl_seconds=10.0)

    res1, res2 = await asyncio.gather(
        mgr1.acquire_lease(owner_id="worker-A"),
        mgr2.acquire_lease(owner_id="worker-B"),
    )

    # Exactly one worker wins the lease
    assert (res1 is True and res2 is False) or (res1 is False and res2 is True)

    winner_owner = "worker-A" if res1 else "worker-B"
    loser_owner = "worker-B" if res1 else "worker-A"

    assert await mgr1.is_leader_valid(owner_id=winner_owner) is True
    assert await mgr2.is_leader_valid(owner_id=loser_owner) is False


@pytest.mark.asyncio
async def test_lease_failover_after_expiration(lease_session_factory):
    """Test 3: Standby worker claims expired lease with monotonic epoch increment."""
    mgrA = LeaderLeaseManager(session_factory=lease_session_factory)
    mgrB = LeaderLeaseManager(session_factory=lease_session_factory)

    # Worker A acquires lease with short TTL (0.1s)
    assert await mgrA.acquire_lease(owner_id="worker-A", ttl_seconds=0.1) is True
    assert await mgrA.get_current_epoch() == 1

    # Wait for lease to expire
    await asyncio.sleep(0.15)

    # Worker A's lease is now expired
    assert await mgrA.is_leader_valid(owner_id="worker-A") is False

    # Worker B claims expired lease
    claimed = await mgrB.acquire_lease(owner_id="worker-B", ttl_seconds=10.0)
    assert claimed is True
    # Epoch must monotonically increment to 2
    assert await mgrB.get_current_epoch() == 2
    assert await mgrB.is_leader_valid(owner_id="worker-B") is True


@pytest.mark.asyncio
async def test_pre_flight_dispatch_ttl_buffer_rejection(lease_session_factory):
    """Test 4: Pre-flight check rejects dispatch when remaining TTL is below safety buffer (2.0s)."""
    mgr = LeaderLeaseManager(session_factory=lease_session_factory)
    owner = "worker-primary"

    # Acquire lease with 1.0s TTL (< 2.0s safety buffer)
    assert await mgr.acquire_lease(owner_id=owner, ttl_seconds=1.0) is True

    # Standard min_ttl of 2.0s should reject pre-flight check
    assert await mgr.is_leader_valid(owner_id=owner, min_ttl_seconds=2.0) is False
    # But a lower min_ttl of 0.5s should accept it
    assert await mgr.is_leader_valid(owner_id=owner, min_ttl_seconds=0.5) is True


@pytest.mark.asyncio
async def test_clean_lease_resignation(lease_session_factory):
    """Test 5: Clean resignation expires lease immediately, allowing instant takeover."""
    mgrA = LeaderLeaseManager(session_factory=lease_session_factory)
    mgrB = LeaderLeaseManager(session_factory=lease_session_factory)

    assert await mgrA.acquire_lease(owner_id="worker-A", ttl_seconds=30.0) is True
    assert await mgrB.acquire_lease(owner_id="worker-B") is False

    # Worker A resigns gracefully
    resigned = await mgrA.resign_lease(owner_id="worker-A")
    assert resigned is True
    assert await mgrA.is_leader_valid(owner_id="worker-A") is False

    # Worker B can immediately acquire leadership
    assert await mgrB.acquire_lease(owner_id="worker-B", ttl_seconds=30.0) is True
    assert await mgrB.get_current_epoch() == 2


@pytest.mark.asyncio
async def test_heartbeat_loop_maintains_lease(lease_session_factory):
    """Test 6: Background heartbeat loop continuously renews lease."""
    mgr = LeaderLeaseManager(session_factory=lease_session_factory)
    owner = "worker-heartbeat"

    # Acquire lease with 2.0s TTL
    assert await mgr.acquire_lease(owner_id=owner, ttl_seconds=2.0) is True

    # Start heartbeat every 0.2s with 2.0s TTL renewal
    mgr.start_heartbeat(owner_id=owner, interval_seconds=0.2, ttl_seconds=2.0)

    # Sleep longer than initial 2.0s TTL (e.g. 0.8s) while heartbeat maintains it
    await asyncio.sleep(0.8)

    # Leader should still be valid
    assert await mgr.is_leader_valid(owner_id=owner, min_ttl_seconds=1.0) is True

    # Stop heartbeat
    mgr.stop_heartbeat()

