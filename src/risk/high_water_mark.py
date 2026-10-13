"""High-water mark manager for live portfolio drawdown calculation and persistence.

Enforces durable tracking in table `account_equity_high_water_mark` for Gate 1
peak-to-trough portfolio drawdown monitoring (6.0% maximum allowed drawdown).
Supports atomic database updates with distributed fencing token validation.
"""

from datetime import datetime, timezone
from typing import Optional, Tuple, Dict
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import logger
from src.database.connection import AsyncSessionLocal
from src.database.models.live import AccountEquityHighWaterMark


class HighWaterMarkError(Exception):
    """Base exception for high-water mark errors."""
    pass


class StaleFencingEpochError(HighWaterMarkError):
    """Raised when an update is attempted with a stale fencing token epoch."""
    pass


class HighWaterMarkManager:
    """Manages high-water mark equity persistence and Gate 1 drawdown calculations."""

    def __init__(self, session_factory=None):
        self._session_factory = session_factory or AsyncSessionLocal
        # In-memory cache: account_id -> peak_equity
        self._cached_peaks: Dict[str, float] = {}
        # In-memory cache: account_id -> fencing_epoch
        self._cached_epochs: Dict[str, int] = {}

    def get_cached_peak(self, account_id: str) -> Optional[float]:
        """Return the currently cached peak equity for an account."""
        return self._cached_peaks.get(account_id)

    def get_cached_epoch(self, account_id: str) -> Optional[int]:
        """Return the currently cached fencing epoch for an account."""
        return self._cached_epochs.get(account_id)

    async def load_or_initialize(
        self,
        account_id: str,
        current_equity: float,
        fencing_epoch: int = 1,
        session: Optional[AsyncSession] = None,
    ) -> float:
        """Load existing high-water mark from database or initialize if absent.

        Args:
            account_id: The live account identifier.
            current_equity: Current total equity of the account.
            fencing_epoch: Monotonic leadership fencing token epoch.
            session: Optional existing AsyncSession.

        Returns:
            The loaded or newly initialized peak equity.
        """
        if current_equity <= 0.0:
            raise ValueError(f"Initial equity must be positive: {current_equity}")

        if session is not None:
            return await self._load_or_init_internal(session, account_id, current_equity, fencing_epoch)

        async with self._session_factory() as sess:
            async with sess.begin():
                return await self._load_or_init_internal(sess, account_id, current_equity, fencing_epoch)

    async def _load_or_init_internal(
        self,
        session: AsyncSession,
        account_id: str,
        current_equity: float,
        fencing_epoch: int,
    ) -> float:
        stmt = select(AccountEquityHighWaterMark).where(
            AccountEquityHighWaterMark.account_id == account_id
        ).with_for_update()
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if record is not None:
            peak = float(record.peak_equity)
            epoch = int(record.fencing_epoch)
            self._cached_peaks[account_id] = peak
            self._cached_epochs[account_id] = epoch
            logger.info(
                f"Loaded persisted high-water mark for {account_id}: "
                f"peak_equity={peak:.2f}, fencing_epoch={epoch}"
            )
            return peak

        # Create new record
        now = datetime.now(timezone.utc)
        record = AccountEquityHighWaterMark(
            account_id=account_id,
            peak_equity=current_equity,
            recorded_at=now,
            fencing_epoch=fencing_epoch,
        )
        session.add(record)
        self._cached_peaks[account_id] = current_equity
        self._cached_epochs[account_id] = fencing_epoch
        logger.info(
            f"Initialized new high-water mark for {account_id}: "
            f"peak_equity={current_equity:.2f}, fencing_epoch={fencing_epoch}"
        )
        return current_equity

    async def update_peak(
        self,
        account_id: str,
        current_equity: float,
        fencing_epoch: int,
        session: Optional[AsyncSession] = None,
    ) -> Tuple[float, bool]:
        """Atomically update high-water mark if current equity exceeds prior peak.

        Enforces fencing epoch monotonically non-decreasing check.

        Args:
            account_id: The live account identifier.
            current_equity: Current total equity to evaluate.
            fencing_epoch: The leadership epoch making the update.
            session: Optional existing AsyncSession.

        Returns:
            Tuple[float, bool]: (peak_equity, was_updated)

        Raises:
            StaleFencingEpochError: If calling fencing_epoch is less than persisted epoch.
        """
        cached_peak = self._cached_peaks.get(account_id)
        if cached_peak is not None and current_equity <= cached_peak:
            return cached_peak, False

        if session is not None:
            return await self._update_peak_internal(session, account_id, current_equity, fencing_epoch)

        async with self._session_factory() as sess:
            async with sess.begin():
                return await self._update_peak_internal(sess, account_id, current_equity, fencing_epoch)

    async def _update_peak_internal(
        self,
        session: AsyncSession,
        account_id: str,
        current_equity: float,
        fencing_epoch: int,
    ) -> Tuple[float, bool]:
        # Lock and read existing record
        stmt = select(AccountEquityHighWaterMark).where(
            AccountEquityHighWaterMark.account_id == account_id
        ).with_for_update()
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if record is None:
            raise HighWaterMarkError(
                f"Cannot update high-water mark: account {account_id} not initialized."
            )

        if fencing_epoch < record.fencing_epoch:
            raise StaleFencingEpochError(
                f"Fencing epoch rejection: caller epoch {fencing_epoch} < persisted epoch {record.fencing_epoch}"
            )

        if current_equity <= record.peak_equity:
            self._cached_peaks[account_id] = float(record.peak_equity)
            self._cached_epochs[account_id] = int(record.fencing_epoch)
            return float(record.peak_equity), False

        # Atomic update
        now = datetime.now(timezone.utc)
        record.peak_equity = current_equity
        record.recorded_at = now
        record.fencing_epoch = fencing_epoch

        self._cached_peaks[account_id] = current_equity
        self._cached_epochs[account_id] = fencing_epoch
        logger.info(
            f"Updated high-water mark for {account_id}: "
            f"new_peak={current_equity:.2f}, fencing_epoch={fencing_epoch}"
        )
        return current_equity, True

    def calculate_drawdown_pct(self, account_id: str, current_equity: float) -> float:
        """Calculate peak-to-trough drawdown percentage.

        Formula:
            DrawdownPct = ((PeakEquity - CurrentEquity) / PeakEquity) * 100.0

        Args:
            account_id: Account identifier.
            current_equity: Current mark-to-market equity.

        Returns:
            Drawdown percentage as float (e.g. 5.5 for 5.5%). Non-negative.
        """
        peak = self._cached_peaks.get(account_id)
        if peak is None or peak <= 0.0:
            return 0.0

        if current_equity >= peak:
            return 0.0

        drawdown = ((peak - current_equity) / peak) * 100.0
        return max(0.0, round(drawdown, 4))

    def is_gate1_breached(
        self,
        account_id: str,
        current_equity: float,
        threshold_pct: float = 6.0,
    ) -> bool:
        """Evaluate if Gate 1 peak-to-trough drawdown cap is breached.

        Args:
            account_id: Account identifier.
            current_equity: Current total equity.
            threshold_pct: Ratified maximum portfolio drawdown (defaults to 6.0%).

        Returns:
            True if DrawdownPct >= threshold_pct, False otherwise.
        """
        drawdown_pct = self.calculate_drawdown_pct(account_id, current_equity)
        return drawdown_pct >= threshold_pct
