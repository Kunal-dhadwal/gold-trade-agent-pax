"""Quad-tier fee management engine with maker rebate support for AGentTrXPAUS."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger("agenttrxpaus.execution.fee_manager")


class FeeManagerError(Exception):
    """Base exception for FeeManager errors."""
    pass


class StaleFeeCacheError(FeeManagerError):
    """Raised when a cached fee schedule has exceeded its TTL (Gate 11 trigger)."""
    pass


class FeeScheduleNotFoundError(FeeManagerError):
    """Raised when no fee schedule exists for an account/product pair."""
    pass


class Tier3FeeForbiddenInLiveError(FeeManagerError):
    """Raised when an unauthenticated Tier 3 estimated fee crosses the live order boundary."""
    pass


class InvalidFeeRateError(FeeManagerError, ValueError):
    """Raised when fee rates fall outside institutional regulatory bounds."""
    pass


@dataclass
class FeeSchedule:
    """Authoritative representation of fee rates for an account and instrument.
    
    Permitted Regulatory Bounds:
    - Maker commission rate: [-0.0010, 0.0500] (-10 bps rebate to +500 bps fee)
    - Taker commission rate: [0.0000, 0.0500] (0 bps to +500 bps fee)
    """

    account_id: str
    product_symbol: str = "PAXGUSDT"
    tier_level: int = 1
    maker_rate: float = 0.0005
    taker_rate: float = 0.0010
    source: str = "REST_AUTHENTICATED_TIER_1"
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ttl_seconds: float = 86400.0  # 24 hours

    def __post_init__(self) -> None:
        if self.fetched_at.tzinfo is None:
            self.fetched_at = self.fetched_at.replace(tzinfo=timezone.utc)
        self.validate()

    def validate(self) -> None:
        """Enforce strict institutional fee rate bounds."""
        # Maker rate validation: -10 bps to +500 bps
        if not (-0.0010 <= self.maker_rate <= 0.0500):
            raise InvalidFeeRateError(
                f"Maker rate {self.maker_rate} out of valid bounds [-0.0010, 0.0500] "
                f"(-10 bps rebate to +500 bps fee) for account '{self.account_id}'"
            )
        # Taker rate validation: 0 bps to +500 bps
        if not (0.0000 <= self.taker_rate <= 0.0500):
            raise InvalidFeeRateError(
                f"Taker rate {self.taker_rate} out of valid bounds [0.0000, 0.0500] "
                f"(0 bps to +500 bps fee) for account '{self.account_id}'"
            )

    def is_expired(self, current_time: Optional[datetime] = None) -> bool:
        """Check if the cached schedule has exceeded its validity TTL."""
        now = current_time or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        elapsed = (now - self.fetched_at).total_seconds()
        return elapsed > self.ttl_seconds


class FeeManager:
    """Manages multi-tier fee resolution, cache lifecycle, and execution validation.
    
    Tier Hierarchy:
    - Tier 1: Authenticated REST /v2/wallet/balances or /v2/profile (Authoritative Account-Specific)
    - Tier 2: Public Product Specification /v2/products (Pre-trade margin fallback)
    - Tier 3: Static / Estimated fallback (STRICTLY FORBIDDEN across live execution boundary)
    """

    def __init__(self, default_ttl_seconds: float = 86400.0):
        self._default_ttl_seconds = default_ttl_seconds
        self._cache: Dict[Tuple[str, str], FeeSchedule] = {}

    def set_schedule(self, schedule: FeeSchedule) -> None:
        """Store or update a validated fee schedule in memory cache."""
        schedule.validate()
        key = (schedule.account_id, schedule.product_symbol)
        self._cache[key] = schedule
        logger.info(
            f"Updated fee schedule for {key}: tier={schedule.tier_level}, "
            f"maker={schedule.maker_rate}, taker={schedule.taker_rate}, source={schedule.source}"
        )

    def get_schedule(
        self,
        account_id: str,
        product_symbol: str = "PAXGUSDT",
        allow_expired: bool = False,
        current_time: Optional[datetime] = None,
    ) -> Optional[FeeSchedule]:
        """Retrieve fee schedule from cache, checking expiry unless allowed."""
        key = (account_id, product_symbol)
        schedule = self._cache.get(key)
        if not schedule:
            return None
        if not allow_expired and schedule.is_expired(current_time=current_time):
            return None
        return schedule

    def calculate_fee(
        self,
        account_id: str,
        product_symbol: str,
        notional: float,
        is_maker: bool,
        enforce_live_boundary: bool = True,
        current_time: Optional[datetime] = None,
    ) -> float:
        """Calculate transaction fee for an order notional value.
        
        Returns:
            Fee amount (positive for fee paid, negative for rebate credited).
            
        Raises:
            FeeScheduleNotFoundError: If no schedule exists in cache.
            StaleFeeCacheError: If schedule is older than TTL (Gate 11 trigger).
            Tier3FeeForbiddenInLiveError: If Tier 3 estimated fee is presented for live order.
            ValueError: If notional value is negative.
        """
        if notional < 0.0:
            raise ValueError(f"Notional value cannot be negative: {notional}")

        key = (account_id, product_symbol)
        schedule = self._cache.get(key)
        if not schedule:
            raise FeeScheduleNotFoundError(
                f"No fee schedule cached for account '{account_id}', product '{product_symbol}'"
            )

        if schedule.is_expired(current_time=current_time):
            raise StaleFeeCacheError(
                f"Fee schedule for account '{account_id}', product '{product_symbol}' expired "
                f"at {schedule.fetched_at.isoformat()} (TTL: {schedule.ttl_seconds}s). Gate 11 Veto."
            )

        if enforce_live_boundary and "TIER_3" in schedule.source.upper():
            raise Tier3FeeForbiddenInLiveError(
                f"SECURITY VETO: Tier 3 estimated fee schedule ({schedule.source}) is strictly "
                f"prohibited across live execution boundary for account '{account_id}'."
            )

        rate = schedule.maker_rate if is_maker else schedule.taker_rate
        return round(notional * rate, 8)

    @staticmethod
    def reconcile_fill_fee(
        expected_fee: float,
        reported_fee: float,
        tolerance: float = 1e-4,
    ) -> Tuple[bool, float]:
        """Compare locally expected fee against exchange matching engine reported fee.
        
        Returns:
            (is_clean, difference)
        """
        diff = abs(expected_fee - reported_fee)
        is_clean = diff <= tolerance
        if not is_clean:
            logger.warning(
                f"FEE_RECONCILIATION_DISCREPANCY: expected={expected_fee:.6f}, "
                f"reported={reported_fee:.6f}, diff={diff:.6f} > tolerance={tolerance:.6f}"
            )
        return is_clean, diff
