"""FastAPI REST routes for Phase 3 Risk Engine and Emergency Kill Switch.

Prefix: /api/v1/risk
Auth: RFC 7519 JWT RBAC (VIEWER, OPERATOR, ADMIN)
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_role
from src.api.schemas import KillSwitchIn, KillSwitchOut, RiskEventOut
from src.database.connection import get_db
from src.database.models.trading import RiskEvent
from src.paper.service import PaperTradingService

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

    return {
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

