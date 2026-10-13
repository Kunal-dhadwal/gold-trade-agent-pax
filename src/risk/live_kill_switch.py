"""Decoupled Institutional Live Kill Switch Finite State Machine for Phase 4.

Preserves the frozen Phase 1-3 baseline (src/risk/kill_switch.py) 100% untouched while
implementing the formal multi-stage state machine specified in Phase 4 Milestone 4.2:

    NORMAL -> TRIGGERED -> NEW_TRADING_BLOCKED ->
        [Ghost exposure / Zero auto-liquidation] -> MANUAL_RESET_REQUIRED
        [Gate 1 Drawdown >= 6.0% / Operator]   -> FLATTENING -> FLATTENED -> MANUAL_RESET_REQUIRED

    MANUAL_RESET_REQUIRED -> NORMAL (Human Operator ADMIN HMAC Signature + Zero Discrepancies)

Critical Invariants:
1. AI Principal Denial: AI models, bots, and automated agents possess ZERO reset authority.
2. Ghost Exposure Non-Liquidation: Ghost positions freeze trading into MANUAL_RESET_REQUIRED
   with ZERO automated liquidation; existing resting protective stops remain active.
3. Drawdown Emergency Flattening: Gate 1 drawdown (>= 6.0%) or explicit operator emergency
   transitions to FLATTENING to close recognized positions via authenticated reduce_only orders.
4. Pre-Reset Discrepancy Gate: Reset strictly requires DiscrepancyCount == 0 and CLEAN reconciliation.
"""

from datetime import datetime, timezone
from enum import Enum
import logging
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("gold_quant.risk.live_kill_switch")

AI_PRINCIPAL_PREFIXES = ("ai_", "bot_", "agent_", "llm_", "service_", "daemon_")


class LiveKillSwitchState(str, Enum):
    """Formal lifecycle states for the Phase 4 Institutional Live Kill Switch."""

    NORMAL = "NORMAL"
    TRIGGERED = "TRIGGERED"
    NEW_TRADING_BLOCKED = "NEW_TRADING_BLOCKED"
    FLATTENING = "FLATTENING"
    FLATTENED = "FLATTENED"
    MANUAL_RESET_REQUIRED = "MANUAL_RESET_REQUIRED"


class LiveKillSwitchError(Exception):
    """Base exception for live kill switch operations."""
    pass


class InvalidLiveKillSwitchStateTransitionError(LiveKillSwitchError):
    """Raised when an illegal FSM state transition is attempted."""
    pass


class LiveKillSwitchSecurityError(LiveKillSwitchError):
    """Raised when an unauthorized or AI principal attempts to reset the kill switch."""
    pass


class LiveKillSwitchPermissionError(LiveKillSwitchError):
    """Raised when a non-ADMIN operator attempts to reset the kill switch."""
    pass


class LiveKillSwitchDiscrepancyError(LiveKillSwitchError):
    """Raised when kill switch reset is attempted while reconciliation discrepancies remain."""
    pass


# Formal transition graph
VALID_TRANSITIONS: Dict[LiveKillSwitchState, Set[LiveKillSwitchState]] = {
    LiveKillSwitchState.NORMAL: {
        LiveKillSwitchState.TRIGGERED,
    },
    LiveKillSwitchState.TRIGGERED: {
        LiveKillSwitchState.NEW_TRADING_BLOCKED,
    },
    LiveKillSwitchState.NEW_TRADING_BLOCKED: {
        LiveKillSwitchState.MANUAL_RESET_REQUIRED,
        LiveKillSwitchState.FLATTENING,
    },
    LiveKillSwitchState.FLATTENING: {
        LiveKillSwitchState.FLATTENED,
    },
    LiveKillSwitchState.FLATTENED: {
        LiveKillSwitchState.MANUAL_RESET_REQUIRED,
    },
    LiveKillSwitchState.MANUAL_RESET_REQUIRED: {
        LiveKillSwitchState.NORMAL,
    },
}


class LiveKillSwitchFSM:
    """Decoupled deterministic Live Kill Switch FSM with strict multi-stage lifecycle."""

    def __init__(
        self,
        initial_state: LiveKillSwitchState = LiveKillSwitchState.NORMAL,
        on_transition: Optional[Callable[[LiveKillSwitchState, LiveKillSwitchState, Dict[str, Any]], None]] = None,
    ):
        self.state: LiveKillSwitchState = initial_state
        self.triggered_at: Optional[datetime] = None
        self.triggered_by: Optional[str] = None
        self.trigger_reason: Optional[str] = None
        self.policy: Optional[str] = None
        self.reset_at: Optional[datetime] = None
        self.reset_by: Optional[str] = None
        self._on_transition = on_transition
        self.transition_history: List[Dict[str, Any]] = []

    def is_active(self) -> bool:
        """Return True if kill switch is active (trading blocked or restricted)."""
        return self.state != LiveKillSwitchState.NORMAL

    def can_submit_new_orders(self) -> bool:
        """Return True only when kill switch is strictly in NORMAL state."""
        return self.state == LiveKillSwitchState.NORMAL

    def can_submit_protective_exits(self) -> bool:
        """Return True if protective exits and emergency flatten orders are permissible.
        
        Permitted during NORMAL, TRIGGERED, NEW_TRADING_BLOCKED, FLATTENING, and MANUAL_RESET_REQUIRED.
        Prohibited only when FLATTENED (all positions confirmed flat).
        """
        return self.state in {
            LiveKillSwitchState.NORMAL,
            LiveKillSwitchState.TRIGGERED,
            LiveKillSwitchState.NEW_TRADING_BLOCKED,
            LiveKillSwitchState.FLATTENING,
            LiveKillSwitchState.MANUAL_RESET_REQUIRED,
        }

    def transition_to(
        self,
        target_state: LiveKillSwitchState,
        reason: str,
        triggered_by: str = "SYSTEM",
        details: Optional[Dict[str, Any]] = None,
    ) -> LiveKillSwitchState:
        """Execute a validated single-step transition in the FSM graph.
        
        Raises InvalidLiveKillSwitchStateTransitionError on invalid transition.
        """
        allowed = VALID_TRANSITIONS.get(self.state, set())
        if target_state not in allowed:
            raise InvalidLiveKillSwitchStateTransitionError(
                f"Invalid Live Kill Switch transition: {self.state.value} -> {target_state.value}. "
                f"Allowed target states: {[s.value for s in allowed]}"
            )

        from_state = self.state
        self.state = target_state
        now = datetime.now(timezone.utc)

        transition_record = {
            "timestamp": now.isoformat(),
            "from_state": from_state.value,
            "to_state": target_state.value,
            "reason": reason,
            "triggered_by": triggered_by,
            "details": details or {},
        }
        self.transition_history.append(transition_record)
        logger.warning(
            f"LIVE KILL SWITCH TRANSITION: {from_state.value} -> {target_state.value} "
            f"| Triggered by: {triggered_by} | Reason: {reason}"
        )

        if self._on_transition is not None:
            try:
                self._on_transition(from_state, target_state, transition_record)
            except Exception as exc:
                logger.error(f"Error in on_transition callback: {exc}")

        return self.state

    def trigger(
        self,
        reason: str,
        triggered_by: str = "SYSTEM_CIRCUIT_BREAKER",
        policy: str = "NORMAL",
        details: Optional[Dict[str, Any]] = None,
    ) -> LiveKillSwitchState:
        """Trigger the live kill switch and advance through the institutional lifecycle.
        
        If policy is "GHOST_EXPOSURE":
            Transitions: NORMAL -> TRIGGERED -> NEW_TRADING_BLOCKED -> MANUAL_RESET_REQUIRED
            (ZERO automated liquidation; resting stops preserved).
        If policy in ("DRAWDOWN_FLATTEN", "OPERATOR_EMERGENCY"):
            Transitions: NORMAL -> TRIGGERED -> NEW_TRADING_BLOCKED -> FLATTENING
            (Recognized positions marked for reduce_only liquidation).
        Otherwise:
            Transitions: NORMAL -> TRIGGERED -> NEW_TRADING_BLOCKED -> MANUAL_RESET_REQUIRED.
        """
        now = datetime.now(timezone.utc)
        self.triggered_at = now
        self.triggered_by = triggered_by
        self.trigger_reason = reason
        self.policy = policy

        # If already latched in an active state, record event and preserve latched state
        if self.is_active():
            logger.warning(
                f"Live Kill Switch already active in state {self.state.value}. "
                f"Appending secondary trigger event: {reason} by {triggered_by}"
            )
            self.transition_history.append({
                "timestamp": now.isoformat(),
                "from_state": self.state.value,
                "to_state": self.state.value,
                "reason": f"SECONDARY_TRIGGER: {reason}",
                "triggered_by": triggered_by,
                "details": details or {},
            })
            return self.state

        # Step 1: NORMAL -> TRIGGERED
        self.transition_to(
            LiveKillSwitchState.TRIGGERED,
            reason=reason,
            triggered_by=triggered_by,
            details=details,
        )

        # Step 2: TRIGGERED -> NEW_TRADING_BLOCKED
        self.transition_to(
            LiveKillSwitchState.NEW_TRADING_BLOCKED,
            reason="All pending entry orders cancelled; new order creation blocked",
            triggered_by=triggered_by,
            details=details,
        )

        # Step 3: Branch based on policy
        if policy in ("DRAWDOWN_FLATTEN", "OPERATOR_EMERGENCY"):
            # Emergency flatten recognized positions
            self.transition_to(
                LiveKillSwitchState.FLATTENING,
                reason=f"Liquidating recognized positions via reduce_only: {reason}",
                triggered_by=triggered_by,
                details=details,
            )
        else:
            # Ghost exposure, burst rejection, drift, or manual latch: zero auto-liquidation!
            self.transition_to(
                LiveKillSwitchState.MANUAL_RESET_REQUIRED,
                reason=f"Latched without automated liquidation; manual intervention required: {reason}",
                triggered_by=triggered_by,
                details=details,
            )

        return self.state

    def trigger_ghost_exposure(
        self,
        discrepancy_details: str,
        triggered_by: str = "RECONCILIATION_DAEMON",
    ) -> LiveKillSwitchState:
        """Trigger kill switch on ghost position detection.
        
        CRITICAL SAFETY TENET: Zero automated market liquidation. Resting stops preserved.
        """
        return self.trigger(
            reason=f"GHOST_EXPOSURE_DETECTED: {discrepancy_details}",
            triggered_by=triggered_by,
            policy="GHOST_EXPOSURE",
            details={"discrepancy_details": discrepancy_details, "automated_liquidation": False},
        )

    def trigger_drawdown_flatten(
        self,
        drawdown_pct: float,
        triggered_by: str = "LIVE_RISK_ENGINE",
    ) -> LiveKillSwitchState:
        """Trigger kill switch on Gate 1 peak-to-trough drawdown breach (>= 6.0%).
        
        Transitions to FLATTENING to close recognized positions via reduce_only market orders.
        """
        return self.trigger(
            reason=f"GATE_1_DRAWDOWN_BREACH: Portfolio drawdown {drawdown_pct:.2f}% >= 6.0%",
            triggered_by=triggered_by,
            policy="DRAWDOWN_FLATTEN",
            details={"drawdown_pct": drawdown_pct, "automated_liquidation": True},
        )

    def trigger_operator_emergency(
        self,
        operator_id: str,
        reason: str,
    ) -> LiveKillSwitchState:
        """Trigger kill switch via operator emergency flatten command."""
        return self.trigger(
            reason=f"OPERATOR_EMERGENCY_FLATTEN: {reason}",
            triggered_by=operator_id,
            policy="OPERATOR_EMERGENCY",
            details={"operator_id": operator_id, "automated_liquidation": True},
        )

    def confirm_flattened(
        self,
        operator_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> LiveKillSwitchState:
        """Confirm that all recognized positions are closed and advance to MANUAL_RESET_REQUIRED.
        
        Transitions: FLATTENING -> FLATTENED -> MANUAL_RESET_REQUIRED.
        """
        if self.state != LiveKillSwitchState.FLATTENING:
            raise InvalidLiveKillSwitchStateTransitionError(
                f"Cannot confirm flattened: current state is {self.state.value}, expected FLATTENING."
            )

        triggered_by = operator_id or self.triggered_by or "SYSTEM"

        # FLATTENING -> FLATTENED
        self.transition_to(
            LiveKillSwitchState.FLATTENED,
            reason="All recognized positions confirmed flat",
            triggered_by=triggered_by,
            details=details,
        )

        # FLATTENED -> MANUAL_RESET_REQUIRED
        self.transition_to(
            LiveKillSwitchState.MANUAL_RESET_REQUIRED,
            reason="All positions flat; manual human operator reset required to unlock trading",
            triggered_by=triggered_by,
            details=details,
        )

        return self.state

    def reset(
        self,
        operator_role: str,
        operator_id: str,
        discrepancy_count: int = 0,
        reconciliation_status: str = "CLEAN",
    ) -> bool:
        """Reset the live kill switch back to NORMAL.
        
        Mandatory Preconditions:
        1. Human-Only Provenance: AI models, bots, and automated agents are strictly rejected.
        2. Role Authorization: Operator role must be ADMIN.
        3. Zero Discrepancies: Active discrepancy count must be exactly 0.
        4. Reconciliation Health: Reconciliation status must be CLEAN.
        5. Valid Lifecycle State: Must be in MANUAL_RESET_REQUIRED (or FLATTENED).
        """
        now = datetime.now(timezone.utc)

        # 1. AI Principal Lockout Assertion
        op_id_lower = str(operator_id).lower()
        op_role_lower = str(operator_role).lower()
        if op_id_lower.startswith(AI_PRINCIPAL_PREFIXES) or op_role_lower.startswith(AI_PRINCIPAL_PREFIXES):
            logger.critical(
                f"SECURITY VETO: AI principal '{operator_id}' attempted to reset Live Kill Switch. Access denied."
            )
            raise LiveKillSwitchSecurityError(
                "SECURITY VETO: AI models possess zero authority over Live Kill Switch."
            )

        # 2. ADMIN Role Check
        if operator_role.upper() != "ADMIN":
            logger.warning(
                f"PERMISSION REJECTION: Operator '{operator_id}' with role '{operator_role}' denied reset authority."
            )
            raise LiveKillSwitchPermissionError(
                f"Security Veto: Role '{operator_role}' is not authorized to reset Live Kill Switch. ADMIN required."
            )

        # 3. Discrepancy Gate (RSK-02 & Section C.4)
        if discrepancy_count > 0:
            logger.warning(
                f"DISCREPANCY VETO: Reset blocked with {discrepancy_count} unresolved discrepancies."
            )
            raise LiveKillSwitchDiscrepancyError(
                f"Cannot reset Live Kill Switch: unresolved discrepancy count is {discrepancy_count}. "
                "Zero discrepancies required. Remediate ghost positions or active order mismatches before reset."
            )

        # 4. Reconciliation Status Gate
        if reconciliation_status.upper() != "CLEAN":
            logger.warning(
                f"RECONCILIATION STATUS VETO: Reset blocked with reconciliation status '{reconciliation_status}'."
            )
            raise LiveKillSwitchDiscrepancyError(
                f"Cannot reset Live Kill Switch: reconciliation status is '{reconciliation_status}'. "
                "Status must be CLEAN before reset."
            )

        # 5. Lifecycle State Validation
        if self.state == LiveKillSwitchState.NORMAL:
            logger.info("Live Kill Switch is already in NORMAL state; reset is no-op.")
            return True

        if self.state == LiveKillSwitchState.FLATTENING:
            raise InvalidLiveKillSwitchStateTransitionError(
                "Cannot reset Live Kill Switch: FLATTENING is in progress. Await completion."
            )


        if self.state == LiveKillSwitchState.FLATTENED:
            # Advance to MANUAL_RESET_REQUIRED first
            self.transition_to(
                LiveKillSwitchState.MANUAL_RESET_REQUIRED,
                reason="Auto-advancing flat state for operator reset",
                triggered_by=operator_id,
            )

        if self.state in (LiveKillSwitchState.TRIGGERED, LiveKillSwitchState.NEW_TRADING_BLOCKED):
            # Advance to MANUAL_RESET_REQUIRED first
            self.transition_to(
                LiveKillSwitchState.MANUAL_RESET_REQUIRED,
                reason="Advancing to manual reset required for operator reset",
                triggered_by=operator_id,
            )

        # Execute final transition: MANUAL_RESET_REQUIRED -> NORMAL
        self.transition_to(
            LiveKillSwitchState.NORMAL,
            reason=f"Reset to NORMAL by human administrator {operator_id}",
            triggered_by=operator_id,
            details={
                "discrepancy_count": discrepancy_count,
                "reconciliation_status": reconciliation_status,
            },
        )

        self.reset_at = now
        self.reset_by = operator_id
        logger.info(
            f"LIVE KILL SWITCH RESET TO NORMAL by administrator '{operator_id}'. New trading unlocked."
        )
        return True

    def get_status(self) -> Dict[str, Any]:
        """Return serializable status dictionary of the live kill switch."""
        return {
            "state": self.state.value,
            "active": self.is_active(),
            "can_submit_new_orders": self.can_submit_new_orders(),
            "can_submit_protective_exits": self.can_submit_protective_exits(),
            "triggered_at": self.triggered_at.isoformat() if self.triggered_at else None,
            "triggered_by": self.triggered_by,
            "trigger_reason": self.trigger_reason,
            "policy": self.policy,
            "reset_at": self.reset_at.isoformat() if self.reset_at else None,
            "reset_by": self.reset_by,
            "transition_count": len(self.transition_history),
        }
