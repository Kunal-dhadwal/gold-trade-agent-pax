"""FastAPI REST routes for Phase 3 Risk Engine and Emergency Kill Switch.

Prefix: /api/v1/risk
Auth: RFC 7519 JWT RBAC (VIEWER, OPERATOR, ADMIN)
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_role
from src.api.schemas import KillSwitchIn, KillSwitchOut, RiskEventOut
from src.database.connection import get_db
from src.database.models.live import ReconciliationSnapshot
from src.database.models.trading import RiskEvent
from src.execution.reconciliation import ReconciliationService
from src.paper.service import PaperTradingService

logger = logging.getLogger("gold_quant.api.risk")

router = APIRouter(prefix="/risk", tags=["Risk & Circuit Breakers"])
service = PaperTradingService.get_instance()


@router.get("/events", response_model=List[RiskEventOut])
async def list_risk_events(
    severity: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role("VIEWER")),
):
    """Query risk alerts and veto events from durable database and in-memory queue."""
    stmt = select(RiskEvent).order_by(RiskEvent.created_at.desc())
    if severity:
        stmt = stmt.where(RiskEvent.severity == severity.upper())
    res = await db.execute(stmt)
    db_events = res.scalars().all()

    out = [
        RiskEventOut(
            event_id=e.id,
            timestamp=e.created_at.isoformat(),
            gate_name=e.event_type,
            severity=e.severity,
            action=e.action_taken,
            details=e.description,
        )
        for e in db_events
    ]
    if not out:
        raw_events = service.get_risk_events(severity=severity)
        out = [
            RiskEventOut(
                event_id=e.get("event_id", str(uuid.uuid4())),
                timestamp=e.get("timestamp", datetime.now(timezone.utc).isoformat()),
                gate_name=e.get("gate_name", "UNKNOWN"),
                severity=e.get("severity", "INFO"),
                action=e.get("action", "LOG"),
                details=e.get("details", ""),
            )
            for e in raw_events
        ]
    return out


@router.get("/status")
async def get_risk_status(_user=Depends(require_role("VIEWER"))):
    """Query current operational state of all 8 risk gates and Kill Switch."""
    engines = service.get_all_engines()
    if not engines:
        default_engine = service.get_or_create_engine("PAPER_ACC_DEFAULT")
        engines = [default_engine]

    eng = engines[0]
    ks_status = eng.risk_engine.kill_switch.get_status()

    res = {
        "status": "NORMAL" if not ks_status.get("active") else ks_status.get("state"),
        "kill_switch": ks_status,
        "gates": {
            "gate_1_max_drawdown": {"limit_pct": eng.risk_engine.max_drawdown_pct, "active": True},
            "gate_2_max_daily_loss": {"limit_pct": eng.risk_engine.max_daily_loss_pct, "active": True},
            "gate_3_risk_per_trade": {"limit_pct": eng.risk_engine.max_risk_per_trade_pct, "active": True},
            "gate_4_position_cap": {"max_open_positions": eng.risk_engine.max_concurrent_positions, "active": True},
            "gate_5_spread_filter": {"max_spread_usd": eng.risk_engine.max_spread_usd, "active": True},
            "gate_6_consecutive_losses": {"max_streak": eng.risk_engine.max_consecutive_losses, "active": True},
            "gate_7_regime_filter": {"active": True},
            "gate_8_emergency_kill_switch": {"active": True, "state": ks_status.get("state")},
        },
    }

    rec_service = ReconciliationService.get_instance()
    live_ks = rec_service.get_live_kill_switch()
    live_risk = rec_service.get_live_risk_engine()
    if live_ks is not None:
        res["live_kill_switch"] = live_ks.get_status()
    if live_risk is not None:
        res["live_risk_engine"] = {
            "account_id": live_risk.account_id,
            "max_drawdown_pct": live_risk.max_drawdown_pct,
            "max_daily_loss_pct": live_risk.max_daily_loss_pct,
            "max_risk_per_trade_pct": live_risk.max_risk_per_trade_pct,
            "max_leverage": live_risk.max_leverage,
            "max_active_positions": live_risk.max_active_positions,
            "consecutive_losses": live_risk.consecutive_losses,
            "current_regime": live_risk.current_regime,
            "quantity_drift": live_risk.quantity_drift,
        }
    return res


@router.get("/live/status")
async def get_live_risk_status(_user=Depends(require_role("VIEWER"))):
    """Query operational status of Phase 4 12-Gate LiveRiskEngine and LiveKillSwitchFSM."""
    rec_service = ReconciliationService.get_instance()
    live_ks = rec_service.get_live_kill_switch()
    live_risk = rec_service.get_live_risk_engine()
    return {
        "live_kill_switch": live_ks.get_status() if live_ks is not None else None,
        "live_risk_engine": {
            "account_id": live_risk.account_id,
            "max_drawdown_pct": live_risk.max_drawdown_pct,
            "max_daily_loss_pct": live_risk.max_daily_loss_pct,
            "max_risk_per_trade_pct": live_risk.max_risk_per_trade_pct,
            "max_leverage": live_risk.max_leverage,
            "max_active_positions": live_risk.max_active_positions,
            "consecutive_losses": live_risk.consecutive_losses,
            "current_regime": live_risk.current_regime,
            "quantity_drift": live_risk.quantity_drift,
        } if live_risk is not None else None,
    }


@router.post("/kill-switch", response_model=KillSwitchOut)
async def trigger_kill_switch(
    data: KillSwitchIn,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("OPERATOR")),
):
    """Trigger the Emergency Kill Switch: immediately flatten all positions and cancel orders."""
    # Check if user is AI
    if str(user.get("sub", "")).lower().startswith(("ai_", "bot_", "agent_", "llm_")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SECURITY VETO: AI models possess zero authority over Kill Switch.",
        )

    engines = service.get_all_engines()
    total_flattened = 0
    total_canceled = 0

    now_str = datetime.now(timezone.utc).isoformat()
    for eng in engines:
        res = await eng.flatten_all(reason=data.reason, triggered_by=data.operator_id)
        total_flattened += res.get("positions_flattened", 0)
        total_canceled += res.get("orders_canceled", 0)

    # Trigger registered LiveKillSwitchFSM if present
    live_ks = ReconciliationService.get_instance().get_live_kill_switch()
    if live_ks is not None:
        live_ks.trigger(
            reason=data.reason,
            triggered_by=data.operator_id,
            policy=data.policy,
        )

    await service.record_risk_event_durable(
        db=db,
        event_type="GATE_8_KILL_SWITCH",
        severity="CRITICAL",
        description=f"Triggered by {data.operator_id}: {data.reason}. {total_flattened} positions flattened.",
        action_taken=f"KILL_SWITCH_{data.policy}",
        snapshot_equity=engines[0].portfolio.get_equity() if engines else 0.0,
        snapshot_drawdown_pct=engines[0].portfolio.get_max_drawdown_pct() if engines else 0.0,
    )

    return KillSwitchOut(
        status="KILL_SWITCH_ACTIVE",
        triggered_at=now_str,
        operator_id=data.operator_id,
        policy=data.policy,
        positions_flattened=total_flattened,
        orders_canceled=total_canceled,
        message=f"EMERGENCY KILL SWITCH LATCHED. {total_flattened} positions flattened, {total_canceled} orders cancelled.",
    )


@router.post("/kill-switch/reset", response_model=KillSwitchOut)
async def reset_kill_switch(
    operator_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role("ADMIN")),
):
    """Reset the Emergency Kill Switch back to NORMAL (Strictly human operator only)."""
    if str(user.get("sub", "")).lower().startswith(("ai_", "bot_", "agent_", "llm_")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SECURITY VETO: AI models possess zero authority to reset Kill Switch.",
        )

    # RECONCILIATION DISCREPANCY GATE (RSK-02 & Section C.4)
    rec_service = ReconciliationService.get_instance()
    active_discrepancies = rec_service.get_discrepancy_count()
    if active_discrepancies > 0:
        logger.warning(
            f"Kill Switch reset blocked by Reconciliation Gate: {active_discrepancies} active discrepancies in memory."
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot reset Kill Switch: unresolved discrepancy count is {active_discrepancies}. "
                "Zero discrepancies required. Remediate ghost positions or active order mismatches before reset."
            ),
        )

    # Trigger immediate full live three-way reconciliation pass if daemon is registered
    daemon = rec_service.get_daemon()
    if daemon is not None:
        try:
            fresh_cycle = await daemon.run_reconciliation_cycle()
            if fresh_cycle.get("discrepancy_count", 0) > 0 or fresh_cycle.get("status") != "CLEAN":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Cannot reset Kill Switch: immediate pre-reset reconciliation cycle detected "
                        f"{fresh_cycle.get('discrepancy_count', 0)} discrepancies (status={fresh_cycle.get('status')}). "
                        "Zero discrepancies required. Remediate ghost positions or active order mismatches before reset."
                    ),
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Error during pre-reset reconciliation cycle: {exc}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Cannot reset Kill Switch: pre-reset reconciliation cycle failed: {exc}",
            )

    # Check durable database authority (latest snapshot)
    now_utc = datetime.now(timezone.utc)
    snap_stmt = select(ReconciliationSnapshot).order_by(ReconciliationSnapshot.timestamp.desc()).limit(1)
    snap_res = await db.execute(snap_stmt)
    latest_snapshot = snap_res.scalar_one_or_none()
    if latest_snapshot is not None:
        if latest_snapshot.discrepancy_count > 0:
            logger.warning(
                f"Kill Switch reset blocked by Database Snapshot Gate: {latest_snapshot.discrepancy_count} discrepancies (status={latest_snapshot.status})."
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot reset Kill Switch: latest database reconciliation snapshot records "
                    f"{latest_snapshot.discrepancy_count} unresolved discrepancies (status={latest_snapshot.status}). "
                    "Zero discrepancies required. Remediate ghost positions before reset."
                ),
            )
        snap_time = latest_snapshot.timestamp.replace(tzinfo=timezone.utc) if latest_snapshot.timestamp.tzinfo is None else latest_snapshot.timestamp
        snap_age = (now_utc - snap_time).total_seconds()
        if daemon is None and snap_age > 60.0:
            logger.warning(f"Kill Switch reset blocked: latest database snapshot is stale ({snap_age:.1f}s > 60.0s)")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot reset Kill Switch: latest database reconciliation snapshot is stale ({snap_age:.1f}s > 60.0s).",
            )

    # Reset registered live order router if present
    live_router = rec_service.get_router()
    if live_router is not None:
        try:
            live_router.reset_kill_switch(operator_role="ADMIN")
        except Exception as exc:
            logger.warning(f"Error resetting live router kill switch: {exc}")

    # Reset registered LiveKillSwitchFSM if present
    live_ks = rec_service.get_live_kill_switch()
    if live_ks is not None:
        try:
            live_ks.reset(
                operator_role="ADMIN",
                operator_id=operator_id,
                discrepancy_count=active_discrepancies,
                reconciliation_status="CLEAN",
            )
        except Exception as exc:
            logger.warning(f"Error resetting LiveKillSwitchFSM: {exc}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Live Kill Switch FSM reset rejected: {exc}",
            )

    engines = service.get_all_engines()
    now_str = datetime.now(timezone.utc).isoformat()
    for eng in engines:
        eng.risk_engine.kill_switch.reset(operator_role="ADMIN", operator_id=operator_id)

    await service.record_risk_event_durable(
        db=db,
        event_type="GATE_8_KILL_SWITCH",
        severity="INFO",
        description=f"Kill switch reset to NORMAL by human admin {operator_id}.",
        action_taken="KILL_SWITCH_RESET",
        snapshot_equity=engines[0].portfolio.get_equity() if engines else 0.0,
        snapshot_drawdown_pct=engines[0].portfolio.get_max_drawdown_pct() if engines else 0.0,
    )

    return KillSwitchOut(
        status="NORMAL",
        triggered_at=now_str,
        operator_id=operator_id,
        policy="NORMAL",
        positions_flattened=0,
        orders_canceled=0,
        message=f"Kill Switch reset to NORMAL by human admin {operator_id}. New trading unlocked.",
    )


