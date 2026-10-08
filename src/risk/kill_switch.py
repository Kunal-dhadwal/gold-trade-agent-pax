"""Emergency Kill Switch Finite State Machine for Phase 3."""

from datetime import datetime, timezone
from enum import Enum
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class KillSwitchState(str, Enum):
    """Formal lifecycle states for the Emergency Kill Switch."""

    NORMAL = "NORMAL"
    KILL_SWITCH_TRIGGERED = "KILL_SWITCH_TRIGGERED"
    NEW_TRADING_BLOCKED = "NEW_TRADING_BLOCKED"
    POSITIONS_FLATTENED = "POSITIONS_FLATTENED"
    MANUAL_RESET_REQUIRED = "MANUAL_RESET_REQUIRED"


class KillSwitchFSM:
    """Deterministic Kill Switch state machine with strict human-only reset authority."""

    def __init__(self):
        self.state: KillSwitchState = KillSwitchState.NORMAL
        self.triggered_at: Optional[datetime] = None
        self.triggered_by: Optional[str] = None
        self.trigger_reason: Optional[str] = None
        self.reset_at: Optional[datetime] = None
        self.reset_by: Optional[str] = None

    def is_active(self) -> bool:
        """Return True if kill switch is active (trading blocked)."""
        return self.state != KillSwitchState.NORMAL

    def trigger(self, reason: str, triggered_by: str = "SYSTEM_CIRCUIT_BREAKER") -> KillSwitchState:
        """Trigger the emergency kill switch and advance through safety latch states."""
        now = datetime.now(timezone.utc)
        self.triggered_at = now
        self.triggered_by = triggered_by
        self.trigger_reason = reason

        # Transition sequence
        self.state = KillSwitchState.KILL_SWITCH_TRIGGERED
        logger.critical(f"KILL SWITCH TRIGGERED by {triggered_by}! Reason: {reason}")

        self.state = KillSwitchState.NEW_TRADING_BLOCKED
        logger.critical("KILL SWITCH: New trading blocked. All pending orders cancelled.")

        self.state = KillSwitchState.POSITIONS_FLATTENED
        logger.critical("KILL SWITCH: Positions flattened via emergency market orders.")

        self.state = KillSwitchState.MANUAL_RESET_REQUIRED
        logger.critical("KILL SWITCH LATCHED: Manual human reset required.")

        return self.state

    def reset(self, operator_role: str, operator_id: str) -> bool:
        """Reset the kill switch back to NORMAL.

        Invariant: Restricted strictly to authenticated human 'ADMIN' or 'OPERATOR'.
        AI agents possess ZERO authority to reset the kill switch.
        """
        role_cleaned = operator_role.strip().upper()
        if role_cleaned not in ("ADMIN", "OPERATOR"):
            logger.warning(
                f"Unauthorized kill switch reset attempt by role '{operator_role}' (id={operator_id}). REJECTED."
            )
            return False

        if self.state != KillSwitchState.MANUAL_RESET_REQUIRED:
            logger.warning(f"Cannot reset kill switch from current state: {self.state}")
            return False

        self.state = KillSwitchState.NORMAL
        self.reset_at = datetime.now(timezone.utc)
        self.reset_by = f"{role_cleaned}:{operator_id}"
        logger.info(f"Kill switch reset to NORMAL by {self.reset_by} at {self.reset_at.isoformat()}")
        return True

    def get_status(self) -> Dict[str, Any]:
        """Return full diagnostic state dictionary."""
        return {
            "state": self.state.value,
            "active": self.is_active(),
            "is_active": self.is_active(),
            "triggered_at": self.triggered_at.isoformat() if self.triggered_at else None,
            "triggered_by": self.triggered_by,
            "trigger_reason": self.trigger_reason,
            "reset_at": self.reset_at.isoformat() if self.reset_at else None,
            "reset_by": self.reset_by,
        }

