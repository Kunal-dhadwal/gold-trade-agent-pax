"""Unit tests for Quad-Tier Fee Management Engine (Sub-Milestone 4.2.1)."""

from datetime import datetime, timedelta, timezone
import pytest

from src.execution.fee_manager import (
    FeeManager,
    FeeSchedule,
    InvalidFeeRateError,
    FeeScheduleNotFoundError,
    StaleFeeCacheError,
    Tier3FeeForbiddenInLiveError,
)


def test_maker_rebate_bounds():
    """Verify regulatory maker rebate bounds [-0.0010, 0.0500] (-10 bps to +500 bps)."""
    # -10 bps maker rebate is valid (institutional maker tier)
    schedule_min_maker = FeeSchedule(
        account_id="ACC_LIVE_INST_01",
        product_symbol="PAXGUSDT",
        tier_level=1,
        maker_rate=-0.0010,
        taker_rate=0.0005,
    )
    assert schedule_min_maker.maker_rate == -0.0010

    # 0 bps maker fee is valid
    schedule_zero_maker = FeeSchedule(
        account_id="ACC_LIVE_INST_01",
        product_symbol="PAXGUSDT",
        tier_level=1,
        maker_rate=0.0000,
        taker_rate=0.0005,
    )
    assert schedule_zero_maker.maker_rate == 0.0000

    # +500 bps maker fee ceiling is valid
    schedule_max_maker = FeeSchedule(
        account_id="ACC_LIVE_INST_01",
        product_symbol="PAXGUSDT",
        tier_level=1,
        maker_rate=0.0500,
        taker_rate=0.0500,
    )
    assert schedule_max_maker.maker_rate == 0.0500

    # Below -10 bps rebate (< -0.0010) must be rejected with InvalidFeeRateError / ValueError
    with pytest.raises(InvalidFeeRateError):
        FeeSchedule(
            account_id="ACC_LIVE_INST_01",
            product_symbol="PAXGUSDT",
            tier_level=1,
            maker_rate=-0.0011,
            taker_rate=0.0005,
        )

    # Above +500 bps (> 0.0500) must be rejected
    with pytest.raises(InvalidFeeRateError):
        FeeSchedule(
            account_id="ACC_LIVE_INST_01",
            product_symbol="PAXGUSDT",
            tier_level=1,
            maker_rate=0.0501,
            taker_rate=0.0005,
        )


def test_taker_fee_bounds():
    """Verify regulatory taker fee bounds [0.0000, 0.0500] (0 bps to +500 bps)."""
    # 0 bps taker is valid
    schedule_zero_taker = FeeSchedule(
        account_id="ACC_LIVE_01",
        product_symbol="PAXGUSDT",
        maker_rate=0.0002,
        taker_rate=0.0000,
    )
    assert schedule_zero_taker.taker_rate == 0.0

    # Negative taker fee (rebate for taking liquidity) is strictly forbidden
    with pytest.raises(InvalidFeeRateError):
        FeeSchedule(
            account_id="ACC_LIVE_01",
            product_symbol="PAXGUSDT",
            maker_rate=0.0002,
            taker_rate=-0.0001,
        )

    # Above +500 bps taker fee is rejected
    with pytest.raises(InvalidFeeRateError):
        FeeSchedule(
            account_id="ACC_LIVE_01",
            product_symbol="PAXGUSDT",
            maker_rate=0.0002,
            taker_rate=0.0501,
        )


def test_fee_schedule_ttl_and_expiry():
    """Verify fee schedule TTL and cache expiry mechanism."""
    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(seconds=3600)

    # Schedule with 1800s TTL fetched 1 hour ago is expired
    expired_schedule = FeeSchedule(
        account_id="ACC_LIVE_01",
        product_symbol="PAXGUSDT",
        fetched_at=one_hour_ago,
        ttl_seconds=1800.0,
    )
    assert expired_schedule.is_expired(current_time=now) is True

    # Schedule with 7200s TTL fetched 1 hour ago is fresh
    fresh_schedule = FeeSchedule(
        account_id="ACC_LIVE_01",
        product_symbol="PAXGUSDT",
        fetched_at=one_hour_ago,
        ttl_seconds=7200.0,
    )
    assert fresh_schedule.is_expired(current_time=now) is False

    # Naive datetime handling in __post_init__ and is_expired
    naive_dt = datetime.now()
    schedule_naive = FeeSchedule(
        account_id="ACC_LIVE_01",
        product_symbol="PAXGUSDT",
        fetched_at=naive_dt,
    )
    assert schedule_naive.fetched_at.tzinfo == timezone.utc
    assert schedule_naive.is_expired(current_time=naive_dt) is False


def test_fee_manager_cache_and_retrieval():
    """Verify cache storage, retrieval, and allow_expired flag."""
    manager = FeeManager()
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(hours=2)

    sched = FeeSchedule(
        account_id="ACC_LIVE_01",
        product_symbol="PAXGUSDT",
        maker_rate=-0.0005,
        taker_rate=0.0005,
        fetched_at=old_time,
        ttl_seconds=3600.0,  # 1 hr TTL
    )
    manager.set_schedule(sched)

    # Active lookup returns None because it's expired
    assert manager.get_schedule("ACC_LIVE_01", "PAXGUSDT", allow_expired=False, current_time=now) is None

    # Lookup allowing expired returns the schedule
    retrieved = manager.get_schedule("ACC_LIVE_01", "PAXGUSDT", allow_expired=True, current_time=now)
    assert retrieved is not None
    assert retrieved.maker_rate == -0.0005

    # Non-existent schedule returns None
    assert manager.get_schedule("NON_EXISTENT", "PAXGUSDT") is None


def test_fee_calculation_with_maker_rebate():
    """Verify fee calculation when maker receives a rebate (negative fee)."""
    manager = FeeManager()
    schedule = FeeSchedule(
        account_id="ACC_LIVE_01",
        product_symbol="PAXGUSDT",
        maker_rate=-0.0005,  # -5 bps rebate
        taker_rate=0.0010,   # +10 bps fee
        ttl_seconds=86400.0,
    )
    manager.set_schedule(schedule)

    notional = 10_000.0

    # Maker order receives rebate of $5.00 -> returned fee is -5.00
    maker_fee = manager.calculate_fee(
        account_id="ACC_LIVE_01",
        product_symbol="PAXGUSDT",
        notional=notional,
        is_maker=True,
    )
    assert maker_fee == -5.0

    # Taker order pays fee of $10.00 -> returned fee is +10.00
    taker_fee = manager.calculate_fee(
        account_id="ACC_LIVE_01",
        product_symbol="PAXGUSDT",
        notional=notional,
        is_maker=False,
    )
    assert taker_fee == 10.0


def test_fee_manager_stale_cache_gate_11_veto():
    """Verify Gate 11 veto: StaleFeeCacheError raised when cached fee is expired."""
    manager = FeeManager()
    now = datetime.now(timezone.utc)
    expired_time = now - timedelta(hours=25)

    schedule = FeeSchedule(
        account_id="ACC_LIVE_01",
        product_symbol="PAXGUSDT",
        fetched_at=expired_time,
        ttl_seconds=86400.0,  # 24 hour TTL
    )
    manager.set_schedule(schedule)

    with pytest.raises(StaleFeeCacheError) as exc_info:
        manager.calculate_fee(
            account_id="ACC_LIVE_01",
            product_symbol="PAXGUSDT",
            notional=5000.0,
            is_maker=True,
            current_time=now,
        )
    assert "Gate 11 Veto" in str(exc_info.value)


def test_fee_manager_missing_schedule_error():
    """Verify FeeScheduleNotFoundError when account/symbol pair is not cached."""
    manager = FeeManager()
    with pytest.raises(FeeScheduleNotFoundError):
        manager.calculate_fee(
            account_id="UNREGISTERED",
            product_symbol="PAXGUSDT",
            notional=1000.0,
            is_maker=True,
        )


def test_fee_manager_negative_notional_rejected():
    """Verify ValueError when notional is negative."""
    manager = FeeManager()
    schedule = FeeSchedule(
        account_id="ACC_LIVE_01",
        product_symbol="PAXGUSDT",
    )
    manager.set_schedule(schedule)

    with pytest.raises(ValueError):
        manager.calculate_fee(
            account_id="ACC_LIVE_01",
            product_symbol="PAXGUSDT",
            notional=-500.0,
            is_maker=True,
        )


def test_tier_3_fee_forbidden_in_live():
    """Verify Tier 3 estimated fee is strictly prohibited from crossing live boundary."""
    manager = FeeManager()
    schedule = FeeSchedule(
        account_id="ACC_LIVE_01",
        product_symbol="PAXGUSDT",
        tier_level=3,
        source="TIER_3_STATIC_ESTIMATE",
        maker_rate=0.0005,
        taker_rate=0.0010,
    )
    manager.set_schedule(schedule)

    # Live order boundary must raise Tier3FeeForbiddenInLiveError
    with pytest.raises(Tier3FeeForbiddenInLiveError) as exc_info:
        manager.calculate_fee(
            account_id="ACC_LIVE_01",
            product_symbol="PAXGUSDT",
            notional=1000.0,
            is_maker=False,
            enforce_live_boundary=True,
        )
    assert "SECURITY VETO" in str(exc_info.value)

    # When enforce_live_boundary is False (e.g. simulation or research), calculation is allowed
    fee = manager.calculate_fee(
        account_id="ACC_LIVE_01",
        product_symbol="PAXGUSDT",
        notional=1000.0,
        is_maker=False,
        enforce_live_boundary=False,
    )
    assert fee == 1.0


def test_fill_fee_reconciliation():
    """Verify fill fee reconciliation and non-mutating discrepancy detection."""
    # Clean match within tolerance
    is_clean, diff = FeeManager.reconcile_fill_fee(expected_fee=1.2500, reported_fee=1.25005, tolerance=1e-4)
    assert is_clean is True
    assert diff < 1e-4

    # Discrepancy beyond tolerance (e.g. $0.05 difference)
    is_clean, diff = FeeManager.reconcile_fill_fee(expected_fee=1.25, reported_fee=1.30, tolerance=1e-4)
    assert is_clean is False
    assert pytest.approx(diff, 1e-6) == 0.05
