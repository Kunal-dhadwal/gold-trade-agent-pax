"""FastAPI REST routes for Live Reconciliation and Authenticated Operator Recovery.

Prefix: /api/v1/reconciliation
Auth: RFC 7519 JWT RBAC (ADMIN role for mutations, VIEWER for status)
Security:
- Mandatory HMAC-SHA256 signature verification over canonical message payload
- Strict AI-principal denial (HTTP 403 Forbidden on ai_*, bot_*, agent_*, llm_*)
- Durable 24-hour nonce consumption / replay prevention via consumed_authorization_nonces
- Fail-fast HTTP 503 on operator flatten if clock drift > 1000ms or transport outage
"""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import require_role
from src.core.config import settings
from src.database.connection import get_db
from src.database.models.live import (
    ConsumedAuthorizationNonce,
    OperatorReconciliationAuditLog,
    ReconciliationSnapshot,
)
from src.execution.delta.types import (
    DeltaOrderSide,
    DeltaTransportOrTimeSyncUnavailableError,
    PositionBoundsExceededError,
)
from src.execution.reconciliation import ReconciliationService

logger = logging.getLogger("gold_quant.api.reconciliation")

router = APIRouter(prefix="/reconciliation", tags=["Reconciliation & Operator Recovery"])


# =============================================================================
# Request and Response Schemas
# =============================================================================


class AdoptGhostPositionIn(BaseModel):
    """Payload for authenticated operator ghost position adoption."""

    account_id: str = Field(..., description="Target live account ID")
    product_id: int = Field(default=1234, description="Delta product ID")
    product_symbol: str = Field(default="PAXGUSDT", description="Target trading symbol")
    adopted_quantity: float = Field(..., description="Signed or absolute quantity to adopt")
    realized_price: float = Field(..., gt=0, description="Realized execution price on exchange")
    operator_id: str = Field(..., description="Human operator identifier")
    reason: str = Field(..., description="Business rationale for manual position adoption")
    nonce: str = Field(..., description="Cryptographic single-use nonce for replay protection")
    timestamp_iso: str = Field(..., description="ISO 8601 UTC timestamp of signature creation")
    hmac_signature: str = Field(..., description="HMAC-SHA256 signature over canonical message")
    idempotency_key: Optional[str] = Field(default=None, description="Optional 24h idempotency key")


class AdoptGhostPositionOut(BaseModel):
    """Response returned upon successful ghost position adoption."""

    status: str
    snapshot_id: Optional[str]
    discrepancy_count: int
    adopted_quantity: float
    realized_price: float
    operator_id: str
    message: str
    timestamp: str


class OperatorFlattenIn(BaseModel):
    """Payload for authenticated in-band emergency flatten."""

    account_id: str = Field(..., description="Target live account ID")
    product_id: int = Field(default=1234, description="Delta product ID")
    product_symbol: str = Field(default="PAXGUSDT", description="Target trading symbol")
    size: float = Field(..., gt=0, description="Quantity to flatten (reduce_only)")
    side: str = Field(..., description="Order side to flatten: BUY or SELL")
    operator_id: str = Field(..., description="Human operator identifier")
    reason: str = Field(..., description="Operational reason for manual flatten")
    nonce: str = Field(..., description="Cryptographic single-use nonce for replay protection")
    timestamp_iso: str = Field(..., description="ISO 8601 UTC timestamp of signature creation")
    hmac_signature: str = Field(..., description="HMAC-SHA256 signature over canonical message")
    idempotency_key: Optional[str] = Field(default=None, description="Optional 24h idempotency key")


class OperatorFlattenOut(BaseModel):
    """Response returned upon successful in-band operator flatten."""

    status: str
    operator_id: str
    product_id: int
    size: float
    side: str
    order_result: Dict[str, Any]
    message: str
    timestamp: str


class ReconciliationStatusOut(BaseModel):
    """Real-time reconciliation status and discrepancy telemetry."""

    status: str
    account_id: str
    product_symbol: str
    discrepancy_count: int
    ghost_positions: List[Dict[str, Any]]
    is_leader: bool
    last_snapshot_id: Optional[str]
    consecutive_transport_failures: int
    timestamp: str


class ReconciliationSnapshotOut(BaseModel):
    """Historical reconciliation audit snapshot record."""

    snapshot_id: str
    account_id: str
    timestamp: Optional[str]
    discrepancy_count: int
    cash_local: float
    cash_exchange: float
    upnl_local: float
    upnl_exchange: float
    status: str
    details: Dict[str, Any]


# =============================================================================
# Cryptographic and Verification Helpers
# =============================================================================


def _verify_human_operator(user: Dict[str, Any], operator_id: str) -> None:
    """Enforce strict constitutional AI lockout."""
    user_sub = str(user.get("sub", "")).lower()
    op_id = operator_id.lower()
    ai_prefixes = ("ai_", "bot_", "agent_", "llm_")
    if user_sub.startswith(ai_prefixes) or op_id.startswith(ai_prefixes):
        logger.critical(f"SECURITY VETO: Denied AI principal invocation: user={user_sub}, operator={op_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="SECURITY VETO: AI models possess zero authority over operator reconciliation actions.",
        )


def _verify_timestamp_freshness(timestamp_iso: str, max_age_seconds: float = 300.0) -> datetime:
    """Ensure signature timestamp is fresh within allowed clock window."""
    try:
        ts = datetime.fromisoformat(timestamp_iso)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ISO 8601 timestamp format: {exc}",
        )

    now_utc = datetime.now(timezone.utc)
    age = abs((now_utc - ts).total_seconds())
    if age > max_age_seconds:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Request signature expired: timestamp age {age:.1f}s exceeds maximum {max_age_seconds}s window.",
        )
    return ts


def _verify_hmac_signature(canonical_message: str, signature: str) -> None:
    """Verify HMAC-SHA256 signature against system secret key."""
    secret = settings.JWT_SECRET_KEY.encode("utf-8")
    expected_sig = hmac.new(
        secret, canonical_message.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature.lower(), expected_sig.lower()):
        logger.warning(f"HMAC verification failed for canonical message: {canonical_message}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid HMAC-SHA256 signature for operator reconciliation action.",
        )


async def _consume_nonce_durable(
    db: AsyncSession,
    nonce: str,
    signer_id: str,
    idempotency_key: Optional[str] = None,
) -> None:
    """Check and atomically consume nonce and optional idempotency key in database for 24h replay protection."""
    # 1. Nonce check
    stmt = select(ConsumedAuthorizationNonce).where(ConsumedAuthorizationNonce.nonce == nonce)
    res = await db.execute(stmt)
    existing = res.scalar_one_or_none()
    if existing is not None:
        logger.warning(f"Replay attack detected: Nonce '{nonce}' already consumed at {existing.consumed_at}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Replay attack detected: Nonce '{nonce}' has already been consumed.",
        )

    # 2. Idempotency key check if provided
    if idempotency_key:
        idemp_token = f"IDEMP:{idempotency_key}"
        idemp_stmt = select(ConsumedAuthorizationNonce).where(ConsumedAuthorizationNonce.nonce == idemp_token)
        idemp_res = await db.execute(idemp_stmt)
        if idemp_res.scalar_one_or_none() is not None:
            logger.warning(f"Idempotency conflict: Key '{idempotency_key}' already processed")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Duplicate request: Action with idempotency key '{idempotency_key}' already processed.",
            )
        db.add(ConsumedAuthorizationNonce(
            nonce=idemp_token,
            signer_id=signer_id,
            consumed_at=datetime.now(timezone.utc),
        ))

    # 3. Add consumed nonce record
    consumed_record = ConsumedAuthorizationNonce(
        nonce=nonce,
        signer_id=signer_id,
        consumed_at=datetime.now(timezone.utc),
    )
    db.add(consumed_record)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.warning(f"Concurrent replay attack or integrity collision for nonce '{nonce}': {exc}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Replay attack detected: Nonce '{nonce}' or idempotency key has already been consumed.",
        ) from exc


# =============================================================================
# Operator Recovery API Endpoints
# =============================================================================


@router.post("/adopt-ghost-position", response_model=AdoptGhostPositionOut)
@router.post("/adopt_ghost_position", response_model=AdoptGhostPositionOut, include_in_schema=False)
async def adopt_ghost_position(
    payload: AdoptGhostPositionIn,
    db: AsyncSession = Depends(get_db),
    user: Dict[str, Any] = Depends(require_role("ADMIN")),
):
    """Adopt an unhedged exchange position into the local database.
    
    Invariants:
    1. RFC 7519 JWT ADMIN role required.
    2. AI models have ZERO authority (HTTP 403 Forbidden).
    3. Mandatory HMAC-SHA256 signature verification over canonical message.
    4. Durable 24h replay attack prevention via consumed_authorization_nonces.
    5. Writes an immutable audit entry in operator_reconciliation_audit_logs.
    """
    # 1. AI Lockout
    _verify_human_operator(user, payload.operator_id)

    # 2. Timestamp freshness
    _verify_timestamp_freshness(payload.timestamp_iso)

    # 3. Canonical HMAC verification
    canonical_msg = (
        f"ADOPT_GHOST_POSITION|{payload.account_id}|{payload.product_id}|"
        f"{payload.adopted_quantity}|{payload.realized_price}|{payload.operator_id}|"
        f"{payload.nonce}|{payload.timestamp_iso}"
    )
    _verify_hmac_signature(canonical_msg, payload.hmac_signature)

    # 4. Durable nonce & idempotency consumption
    await _consume_nonce_durable(db, payload.nonce, payload.operator_id, payload.idempotency_key)

    # 5. Delegate to active ReconciliationDaemon
    rec_service = ReconciliationService.get_instance()
    daemon = rec_service.get_daemon()
    if daemon is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reconciliation daemon is not currently active.",
        )

    res = await daemon.adopt_ghost_position(
        operator_id=payload.operator_id,
        product_id=payload.product_id,
        adopted_quantity=payload.adopted_quantity,
        realized_price=payload.realized_price,
        notes=payload.reason,
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    return AdoptGhostPositionOut(
        status="ADOPTED",
        snapshot_id=res.get("snapshot_id"),
        discrepancy_count=res.get("discrepancy_count", 0),
        adopted_quantity=payload.adopted_quantity,
        realized_price=payload.realized_price,
        operator_id=payload.operator_id,
        message=f"Ghost position successfully adopted by {payload.operator_id}. Local DB state synchronized.",
        timestamp=now_iso,
    )


@router.post("/operator-flatten", response_model=OperatorFlattenOut)
@router.post("/operator_flatten", response_model=OperatorFlattenOut, include_in_schema=False)
async def operator_flatten(
    payload: OperatorFlattenIn,
    db: AsyncSession = Depends(get_db),
    user: Dict[str, Any] = Depends(require_role("ADMIN")),
):
    """Dispatch an authenticated reduce_only market order to emergency-flatten open exposure.
    
    Fail-Fast Guard:
    Returns HTTP 503 if clock drift > 1000ms or exchange transport is unavailable.
    """
    # 1. AI Lockout
    _verify_human_operator(user, payload.operator_id)

    # 2. Timestamp freshness
    _verify_timestamp_freshness(payload.timestamp_iso)

    # 3. Canonical HMAC verification
    side_str = payload.side.strip().upper()
    if side_str not in ("BUY", "SELL"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid order side '{payload.side}': must be BUY or SELL.",
        )

    canonical_msg = (
        f"OPERATOR_FLATTEN|{payload.account_id}|{payload.product_id}|"
        f"{payload.size}|{side_str}|{payload.operator_id}|"
        f"{payload.nonce}|{payload.timestamp_iso}"
    )
    _verify_hmac_signature(canonical_msg, payload.hmac_signature)

    # 4. Durable nonce & idempotency consumption
    await _consume_nonce_durable(db, payload.nonce, payload.operator_id, payload.idempotency_key)

    # 5. Delegate to active LiveOrderRouter
    rec_service = ReconciliationService.get_instance()
    router_inst = rec_service.get_router()
    if router_inst is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live order router is not currently active.",
        )

    order_side = DeltaOrderSide.BUY if side_str == "BUY" else DeltaOrderSide.SELL

    try:
        order_res = await router_inst.operator_flatten(
            product_id=payload.product_id,
            size=payload.size,
            side=order_side,
        )
    except DeltaTransportOrTimeSyncUnavailableError as exc:
        logger.critical(f"Fail-fast guard triggered on /operator-flatten: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except PositionBoundsExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Position bounds exceeded during flatten: {exc}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # Record audit log
    audit_log = OperatorReconciliationAuditLog(
        log_id=str(uuid.uuid4()),
        snapshot_id=None,
        action_type="OPERATOR_FLATTEN",
        operator_id=payload.operator_id,
        parameters=json.dumps({
            "product_id": payload.product_id,
            "size": payload.size,
            "side": side_str,
            "reason": payload.reason,
            "order_id": order_res.get("client_order_id"),
        }),
        timestamp=datetime.now(timezone.utc),
    )
    db.add(audit_log)
    await db.commit()

    now_iso = datetime.now(timezone.utc).isoformat()
    return OperatorFlattenOut(
        status="FLATTEN_DISPATCHED",
        operator_id=payload.operator_id,
        product_id=payload.product_id,
        size=payload.size,
        side=side_str,
        order_result=order_res,
        message=f"Operator emergency flatten dispatched by {payload.operator_id}.",
        timestamp=now_iso,
    )


@router.get("/status", response_model=ReconciliationStatusOut)
async def get_reconciliation_status(
    user: Dict[str, Any] = Depends(require_role("VIEWER")),
):
    """Query current operational status, discrepancy count, and ghost position alerts."""
    rec_service = ReconciliationService.get_instance()
    daemon = rec_service.get_daemon()
    if daemon is not None:
        status_dict = await daemon.get_status()
        return ReconciliationStatusOut(**status_dict)

    now_iso = datetime.now(timezone.utc).isoformat()
    return ReconciliationStatusOut(
        status="UNINITIALIZED",
        account_id="ACC_LIVE_PRIMARY",
        product_symbol="PAXGUSDT",
        discrepancy_count=0,
        ghost_positions=[],
        is_leader=False,
        last_snapshot_id=None,
        consecutive_transport_failures=0,
        timestamp=now_iso,
    )


@router.get("/snapshots", response_model=List[ReconciliationSnapshotOut])
async def list_reconciliation_snapshots(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: Dict[str, Any] = Depends(require_role("VIEWER")),
):
    """Retrieve historical reconciliation audit snapshots."""
    rec_service = ReconciliationService.get_instance()
    daemon = rec_service.get_daemon()
    if daemon is not None:
        snapshots = await daemon.get_snapshots(limit=limit, offset=offset)
        return [ReconciliationSnapshotOut(**s) for s in snapshots]

    stmt = (
        select(ReconciliationSnapshot)
        .order_by(ReconciliationSnapshot.timestamp.desc())
        .limit(limit)
        .offset(offset)
    )
    res = await db.execute(stmt)
    rows = res.scalars().all()
    return [
        ReconciliationSnapshotOut(
            snapshot_id=s.snapshot_id,
            account_id=s.account_id,
            timestamp=s.timestamp.isoformat() if s.timestamp else None,
            discrepancy_count=s.discrepancy_count,
            cash_local=s.cash_local,
            cash_exchange=s.cash_exchange,
            upnl_local=s.upnl_local,
            upnl_exchange=s.upnl_exchange,
            status=s.status,
            details=json.loads(s.details) if s.details else {},
        )
        for s in rows
    ]

