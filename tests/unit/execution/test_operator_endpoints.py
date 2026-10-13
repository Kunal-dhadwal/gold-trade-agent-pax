"""Unit tests for Authenticated Reconciliation Operator Endpoints (Sub-Milestone 4.2.4).

Validates:
- RFC 7519 JWT RBAC authorization: ADMIN required for mutations, VIEWER permitted for status.
- Strict AI-principal denial (HTTP 403 Forbidden on ai_*, bot_*, agent_*, llm_*).
- Canonical HMAC-SHA256 signature verification over request payload.
- Durable 24-hour single-use nonce consumption and replay protection (HTTP 409 Conflict).
- In-band operator flatten fail-fast guard (HTTP 503 on clock drift > 1000ms or transport outage).
- Discrepancy gate on Kill Switch reset (HTTP 409 Conflict on active discrepancies).
- Ghost-position adoption and audit log persistence in operator_reconciliation_audit_logs.
- Telemetry queries (/status, /snapshots).
"""

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import uuid
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.api.auth import create_access_token
from src.api.main import app
from src.core.config import settings
from src.database.connection import get_db
from src.database.models.base import Base
from src.database.models.instruments import Instrument
from src.database.models.live import (
    ConsumedAuthorizationNonce,
    LiveAccount,
    OperatorReconciliationAuditLog,
    ReconciliationSnapshot,
)
from src.database.models.trading import Position
from src.execution.delta.client import DeltaRestClient
from src.execution.delta.mock_server import MockDeltaExchangeServer
from src.execution.delta.order_router import LiveOrderRouter
from src.execution.delta.types import DeltaConfig, DeltaEnvironment
from src.execution.reconciliation import ReconciliationDaemon, ReconciliationService


def generate_hmac(canonical_message: str) -> str:
    """Generate expected HMAC-SHA256 signature using test secret."""
    secret = settings.JWT_SECRET_KEY.encode("utf-8")
    return hmac.new(secret, canonical_message.encode("utf-8"), hashlib.sha256).hexdigest()


@pytest_asyncio.fixture
async def db_session_factory():
    """Create an isolated in-memory SQLite database sharing state via StaticPool."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Seed initial test fixtures
    async with session_maker() as session:
        inst = Instrument(
            id="PAXGUSDT",
            symbol="PAXGUSDT",
            name="PAX Gold Perpetual",
            asset_class="COMMODITY",
            quote_currency="USDT",
        )
        session.add(inst)

        acc = LiveAccount(
            account_id="ACC_LIVE_PRIMARY",
            name="Primary Live Trading Account",
            currency="USDT",
            initial_balance=50000.0,
            current_cash=50000.0,
            status="ACTIVE",
        )
        session.add(acc)
        await session.commit()

    yield session_maker
    await engine.dispose()


@pytest.fixture
def mock_delta():
    """Create configured MockDeltaExchangeServer and DeltaRestClient."""
    server = MockDeltaExchangeServer(enforce_signatures=True)
    server.balances = {"USDT": 50000.0}
    transport = server.create_mock_transport()
    config = DeltaConfig(
        environment=DeltaEnvironment.MOCK,
        base_url="https://mock.delta.exchange",
        api_key=server.api_key,
        api_secret=server.api_secret,
        live_trading_enabled=False,
    )
    client = DeltaRestClient(config=config, transport=transport)
    return server, client


@pytest_asyncio.fixture
async def async_app_client(db_session_factory):
    """Provide httpx.AsyncClient hooked to FastAPI app with overridden DB session."""
    async def override_get_db():
        async with db_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
    ReconciliationService.reset_instance()


# =============================================================================
# 1. JWT Authentication and RBAC Tests
# =============================================================================


@pytest.mark.asyncio
async def test_auth_jwt_roles_and_token_validation(async_app_client):
    """Test 1: RFC 7519 JWT RBAC authorization gating."""
    ac = async_app_client

    # 1. Missing Authorization header fails with 401
    res = await ac.get("/api/v1/reconciliation/status")
    assert res.status_code == 401

    # 2. Malformed token fails with 401
    res = await ac.get(
        "/api/v1/reconciliation/status",
        headers={"Authorization": "Bearer not.a.valid.jwt"},
    )
    assert res.status_code == 401

    # 3. VIEWER token has read access to /status and /snapshots
    viewer_token = create_access_token("human_viewer", role="VIEWER")
    res_status = await ac.get(
        "/api/v1/reconciliation/status",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert res_status.status_code == 200

    res_snaps = await ac.get(
        "/api/v1/reconciliation/snapshots",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert res_snaps.status_code == 200

    # 4. VIEWER role cannot access mutation endpoints (requires ADMIN) -> 403
    now_iso = datetime.now(timezone.utc).isoformat()
    canonical_msg = f"ADOPT_GHOST_POSITION|ACC_LIVE_PRIMARY|1234|1.0|2700.0|human_operator|nonce-1|{now_iso}"
    sig = generate_hmac(canonical_msg)
    payload = {
        "account_id": "ACC_LIVE_PRIMARY",
        "product_id": 1234,
        "product_symbol": "PAXGUSDT",
        "adopted_quantity": 1.0,
        "realized_price": 2700.0,
        "operator_id": "human_operator",
        "reason": "Test adoption",
        "nonce": "nonce-1",
        "timestamp_iso": now_iso,
        "hmac_signature": sig,
    }

    res_adopt_viewer = await ac.post(
        "/api/v1/reconciliation/adopt-ghost-position",
        json=payload,
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert res_adopt_viewer.status_code == 403

    # 5. OPERATOR role also cannot access mutation endpoints (requires ADMIN) -> 403
    operator_token = create_access_token("human_operator", role="OPERATOR")
    res_adopt_op = await ac.post(
        "/api/v1/reconciliation/adopt-ghost-position",
        json=payload,
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert res_adopt_op.status_code == 403


# =============================================================================
# 2. Strict AI Principal Denial Tests
# =============================================================================


@pytest.mark.asyncio
async def test_strict_ai_principal_denial(async_app_client):
    """Test 2: Constitutional AI Lockout rejects AI identifiers with HTTP 403."""
    ac = async_app_client

    # 1. Token creation itself rejects AI identities
    with pytest.raises(PermissionError):
        create_access_token("ai_trading_agent", role="ADMIN")
    with pytest.raises(PermissionError):
        create_access_token("bot_execution_engine", role="ADMIN")
    with pytest.raises(PermissionError):
        create_access_token("agent_subsystem", role="ADMIN")

    # 2. Even with human token, if operator_id in body starts with AI prefix -> 403
    admin_token = create_access_token("human_lead_admin", role="ADMIN")
    now_iso = datetime.now(timezone.utc).isoformat()

    for ai_name in ("ai_quant", "bot_executor", "agent_router", "llm_evaluator"):
        canonical_msg = f"ADOPT_GHOST_POSITION|ACC_LIVE_PRIMARY|1234|1.0|2700.0|{ai_name}|nonce-ai|{now_iso}"
        sig = generate_hmac(canonical_msg)
        payload = {
            "account_id": "ACC_LIVE_PRIMARY",
            "product_id": 1234,
            "product_symbol": "PAXGUSDT",
            "adopted_quantity": 1.0,
            "realized_price": 2700.0,
            "operator_id": ai_name,
            "reason": "AI automated recovery attempt",
            "nonce": f"nonce-ai-{ai_name}",
            "timestamp_iso": now_iso,
            "hmac_signature": sig,
        }
        res = await ac.post(
            "/api/v1/reconciliation/adopt-ghost-position",
            json=payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status_code == 403
        assert "SECURITY VETO" in res.json()["detail"]


# =============================================================================
# 3. HMAC Signature Verification and Tampering Tests
# =============================================================================


@pytest.mark.asyncio
async def test_hmac_signature_verification_and_tampering(async_app_client):
    """Test 3: Canonical HMAC signature verification and tampering prevention."""
    ac = async_app_client
    admin_token = create_access_token("human_admin", role="ADMIN")
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Invalid signature string fails with 401
    payload = {
        "account_id": "ACC_LIVE_PRIMARY",
        "product_id": 1234,
        "product_symbol": "PAXGUSDT",
        "adopted_quantity": 1.0,
        "realized_price": 2700.0,
        "operator_id": "human_admin",
        "reason": "Test adoption",
        "nonce": "nonce-hmac-1",
        "timestamp_iso": now_iso,
        "hmac_signature": "0000000000000000000000000000000000000000000000000000000000000000",
    }
    res = await ac.post(
        "/api/v1/reconciliation/adopt-ghost-position",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 401
    assert "Invalid HMAC-SHA256 signature" in res.json()["detail"]

    # 2. Tampered payload: signature signed for quantity 1.0, but payload quantity is 5.0 -> 401
    valid_sig_for_1 = generate_hmac(
        f"ADOPT_GHOST_POSITION|ACC_LIVE_PRIMARY|1234|1.0|2700.0|human_admin|nonce-hmac-2|{now_iso}"
    )
    payload_tampered = {
        "account_id": "ACC_LIVE_PRIMARY",
        "product_id": 1234,
        "product_symbol": "PAXGUSDT",
        "adopted_quantity": 5.0,  # Tampered!
        "realized_price": 2700.0,
        "operator_id": "human_admin",
        "reason": "Tampered quantity",
        "nonce": "nonce-hmac-2",
        "timestamp_iso": now_iso,
        "hmac_signature": valid_sig_for_1,
    }
    res_tampered = await ac.post(
        "/api/v1/reconciliation/adopt-ghost-position",
        json=payload_tampered,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_tampered.status_code == 401

    # 3. Expired timestamp (> 300 seconds old) fails with 400
    expired_ts = (datetime.now(timezone.utc) - timedelta(seconds=350)).isoformat()
    sig_expired = generate_hmac(
        f"ADOPT_GHOST_POSITION|ACC_LIVE_PRIMARY|1234|1.0|2700.0|human_admin|nonce-hmac-3|{expired_ts}"
    )
    payload_expired = {
        "account_id": "ACC_LIVE_PRIMARY",
        "product_id": 1234,
        "product_symbol": "PAXGUSDT",
        "adopted_quantity": 1.0,
        "realized_price": 2700.0,
        "operator_id": "human_admin",
        "reason": "Expired timestamp",
        "nonce": "nonce-hmac-3",
        "timestamp_iso": expired_ts,
        "hmac_signature": sig_expired,
    }
    res_expired = await ac.post(
        "/api/v1/reconciliation/adopt-ghost-position",
        json=payload_expired,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_expired.status_code == 400
    assert "Request signature expired" in res_expired.json()["detail"]


# =============================================================================
# 4. Durable Nonce Consumption and Replay Protection Tests
# =============================================================================


@pytest.mark.asyncio
async def test_durable_nonce_consumption_and_replay_protection(
    async_app_client, db_session_factory, mock_delta
):
    """Test 4: Durable 24h replay attack prevention rejects replayed nonces with HTTP 409."""
    ac = async_app_client
    server, client = mock_delta
    admin_token = create_access_token("human_admin", role="ADMIN")
    now_iso = datetime.now(timezone.utc).isoformat()

    # Set up and register daemon
    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
    )
    rec_service = ReconciliationService.get_instance()
    rec_service.register_daemon(daemon)

    nonce = f"unique-nonce-{uuid.uuid4().hex}"
    canonical_msg = f"ADOPT_GHOST_POSITION|ACC_LIVE_PRIMARY|1234|1.0|2700.0|human_admin|{nonce}|{now_iso}"
    sig = generate_hmac(canonical_msg)

    payload = {
        "account_id": "ACC_LIVE_PRIMARY",
        "product_id": 1234,
        "product_symbol": "PAXGUSDT",
        "adopted_quantity": 1.0,
        "realized_price": 2700.0,
        "operator_id": "human_admin",
        "reason": "First valid execution",
        "nonce": nonce,
        "timestamp_iso": now_iso,
        "hmac_signature": sig,
    }

    # First request succeeds
    res1 = await ac.post(
        "/api/v1/reconciliation/adopt-ghost-position",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res1.status_code == 200
    assert res1.json()["status"] == "ADOPTED"

    # Second request with identical nonce is rejected with HTTP 409 Conflict
    res2 = await ac.post(
        "/api/v1/reconciliation/adopt-ghost-position",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res2.status_code == 409
    assert "Replay attack detected" in res2.json()["detail"]

    # Verify nonce was recorded in database
    async with db_session_factory() as session:
        stmt = select(ConsumedAuthorizationNonce).where(ConsumedAuthorizationNonce.nonce == nonce)
        row = (await session.execute(stmt)).scalar_one_or_none()
        assert row is not None
        assert row.signer_id == "human_admin"


# =============================================================================
# 5. Operator Flatten Fail-Fast Guard Tests
# =============================================================================


@pytest.mark.asyncio
async def test_operator_flatten_fail_fast_clock_drift_and_outage(
    async_app_client, db_session_factory, mock_delta
):
    """Test 5: Operator flatten fails fast with HTTP 503 on clock drift or transport outage."""
    ac = async_app_client
    server, client = mock_delta
    admin_token = create_access_token("human_admin", role="ADMIN")
    now_iso = datetime.now(timezone.utc).isoformat()

    router = LiveOrderRouter(client=client, session_factory=db_session_factory)
    rec_service = ReconciliationService.get_instance()
    rec_service.register_router(router)

    # 1. Excessive clock drift (> 1000ms) triggers HTTP 503
    async def mock_clock_drift():
        return 1500.0  # 1500ms drift exceeds 1000ms limit

    router._get_clock_drift_ms = mock_clock_drift

    # Also make /v2/time probe fail
    async def mock_fail_server_time():
        raise RuntimeError("Delta time probe unreachable")

    client.get_server_time = mock_fail_server_time

    nonce1 = f"nonce-flatten-drift-{uuid.uuid4().hex}"
    canonical_msg1 = f"OPERATOR_FLATTEN|ACC_LIVE_PRIMARY|1234|2.0|SELL|human_admin|{nonce1}|{now_iso}"
    sig1 = generate_hmac(canonical_msg1)

    payload_drift = {
        "account_id": "ACC_LIVE_PRIMARY",
        "product_id": 1234,
        "product_symbol": "PAXGUSDT",
        "size": 2.0,
        "side": "SELL",
        "operator_id": "human_admin",
        "reason": "Emergency flatten test",
        "nonce": nonce1,
        "timestamp_iso": now_iso,
        "hmac_signature": sig1,
    }

    res_drift = await ac.post(
        "/api/v1/reconciliation/operator-flatten",
        json=payload_drift,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_drift.status_code == 503
    assert "In-band automated flatten unavailable" in res_drift.json()["detail"]

    # 2. Reset clock sync; test transport outage -> HTTP 503
    async def mock_clock_drift_ok():
        return 50.0  # 50ms ok

    router._get_clock_drift_ms = mock_clock_drift_ok

    async def mock_transport_fail():
        raise RuntimeError("Delta REST connection refused")

    client.get_products = mock_transport_fail

    nonce2 = f"nonce-flatten-outage-{uuid.uuid4().hex}"
    canonical_msg2 = f"OPERATOR_FLATTEN|ACC_LIVE_PRIMARY|1234|2.0|SELL|human_admin|{nonce2}|{now_iso}"
    sig2 = generate_hmac(canonical_msg2)

    payload_outage = {
        "account_id": "ACC_LIVE_PRIMARY",
        "product_id": 1234,
        "product_symbol": "PAXGUSDT",
        "size": 2.0,
        "side": "SELL",
        "operator_id": "human_admin",
        "reason": "Transport outage test",
        "nonce": nonce2,
        "timestamp_iso": now_iso,
        "hmac_signature": sig2,
    }

    res_outage = await ac.post(
        "/api/v1/reconciliation/operator-flatten",
        json=payload_outage,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_outage.status_code == 503

    # 3. Invalid order side -> HTTP 400
    nonce3 = f"nonce-flatten-side-{uuid.uuid4().hex}"
    canonical_msg3 = f"OPERATOR_FLATTEN|ACC_LIVE_PRIMARY|1234|2.0|HOLD|human_admin|{nonce3}|{now_iso}"
    sig3 = generate_hmac(canonical_msg3)

    payload_invalid_side = {
        "account_id": "ACC_LIVE_PRIMARY",
        "product_id": 1234,
        "product_symbol": "PAXGUSDT",
        "size": 2.0,
        "side": "HOLD",
        "operator_id": "human_admin",
        "reason": "Invalid side test",
        "nonce": nonce3,
        "timestamp_iso": now_iso,
        "hmac_signature": sig3,
    }

    res_side = await ac.post(
        "/api/v1/reconciliation/operator-flatten",
        json=payload_invalid_side,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_side.status_code == 400


# =============================================================================
# 6. Ghost Position Adoption and Audit Log Tests
# =============================================================================


@pytest.mark.asyncio
async def test_ghost_position_adoption_and_audit_log(
    async_app_client, db_session_factory, mock_delta
):
    """Test 6: Ghost position adoption synchronizes DB state and logs audit record."""
    ac = async_app_client
    server, client = mock_delta
    admin_token = create_access_token("human_admin", role="ADMIN")
    now_iso = datetime.now(timezone.utc).isoformat()

    server.positions[1234] = {"product_id": 1234, "size": 3.0, "unrealized_pnl": 75.0}

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
    )
    rec_service = ReconciliationService.get_instance()
    rec_service.register_daemon(daemon)

    # Initial cycle flags ghost position
    summary = await daemon.run_reconciliation_cycle()
    assert summary["status"] == "GHOST_POSITION_DETECTED"
    assert rec_service.get_discrepancy_count() == 1

    nonce = f"nonce-adopt-{uuid.uuid4().hex}"
    canonical_msg = f"ADOPT_GHOST_POSITION|ACC_LIVE_PRIMARY|1234|3.0|2680.0|human_admin|{nonce}|{now_iso}"
    sig = generate_hmac(canonical_msg)

    payload = {
        "account_id": "ACC_LIVE_PRIMARY",
        "product_id": 1234,
        "product_symbol": "PAXGUSDT",
        "adopted_quantity": 3.0,
        "realized_price": 2680.0,
        "operator_id": "human_admin",
        "reason": "Confirmed telephone fill with Delta Desk",
        "nonce": nonce,
        "timestamp_iso": now_iso,
        "hmac_signature": sig,
    }

    res = await ac.post(
        "/api/v1/reconciliation/adopt-ghost-position",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res.status_code == 200
    res_data = res.json()
    assert res_data["status"] == "ADOPTED"
    assert res_data["discrepancy_count"] == 0

    # Verify audit log in database
    async with db_session_factory() as session:
        stmt = select(OperatorReconciliationAuditLog).where(
            OperatorReconciliationAuditLog.action_type == "ADOPT_GHOST_POSITION"
        )
        audit = (await session.execute(stmt)).scalar_one_or_none()
        assert audit is not None
        assert audit.operator_id == "human_admin"
        params = json.loads(audit.parameters)
        assert params["adopted_quantity"] == 3.0
        assert params["realized_price"] == 2680.0

        # Verify DB position was created
        pos_stmt = select(Position).where(Position.live_account_id == "ACC_LIVE_PRIMARY")
        pos = (await session.execute(pos_stmt)).scalar_one_or_none()
        assert pos is not None
        assert pos.quantity == 3.0
        assert pos.direction == "BUY"


# =============================================================================
# 7. Kill Switch Reset Discrepancy Gate Tests
# =============================================================================


@pytest.mark.asyncio
async def test_kill_switch_reset_discrepancy_gate(
    async_app_client, db_session_factory, mock_delta
):
    """Test 7: Kill Switch reset returns HTTP 409 Conflict if discrepancies exist."""
    ac = async_app_client
    server, client = mock_delta
    admin_token = create_access_token("human_admin", role="ADMIN")

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
    )
    rec_service = ReconciliationService.get_instance()
    rec_service.register_daemon(daemon)

    # 1. Inject active discrepancy in daemon
    daemon.discrepancy_count = 2

    res_blocked = await ac.post(
        "/api/v1/risk/kill-switch/reset?operator_id=human_admin",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_blocked.status_code == 409
    assert "unresolved discrepancy count is 2" in res_blocked.json()["detail"]

    # 2. Unregister daemon and inject database snapshot with discrepancy to test durable DB gate
    rec_service.register_daemon(None)

    async with db_session_factory() as session:
        bad_snap = ReconciliationSnapshot(
            snapshot_id="snap-bad-1",
            account_id="ACC_LIVE_PRIMARY",
            timestamp=datetime.now(timezone.utc),
            discrepancy_count=1,
            cash_local=50000.0,
            cash_exchange=50000.0,
            upnl_local=0.0,
            upnl_exchange=0.0,
            status="GHOST_POSITION_DETECTED",
            details="{}",
        )
        session.add(bad_snap)
        await session.commit()

    res_db_blocked = await ac.post(
        "/api/v1/risk/kill-switch/reset?operator_id=human_admin",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_db_blocked.status_code == 409
    assert "latest database reconciliation snapshot records 1 unresolved discrepancies" in res_db_blocked.json()["detail"]

    # 3. Add clean snapshot -> reset succeeds with HTTP 200 NORMAL
    async with db_session_factory() as session:
        clean_snap = ReconciliationSnapshot(
            snapshot_id="snap-clean-1",
            account_id="ACC_LIVE_PRIMARY",
            timestamp=datetime.now(timezone.utc) + timedelta(seconds=1),
            discrepancy_count=0,
            cash_local=50000.0,
            cash_exchange=50000.0,
            upnl_local=0.0,
            upnl_exchange=0.0,
            status="CLEAN",
            details="{}",
        )
        session.add(clean_snap)
        await session.commit()

    res_ok = await ac.post(
        "/api/v1/risk/kill-switch/reset?operator_id=human_admin",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_ok.status_code == 200
    assert res_ok.json()["status"] == "NORMAL"


# =============================================================================
# 8. Status and Snapshots Endpoints Tests
# =============================================================================


@pytest.mark.asyncio
async def test_status_and_snapshots_endpoints(
    async_app_client, db_session_factory, mock_delta
):
    """Test 8: Telemetry endpoints /status and /snapshots return accurate state."""
    ac = async_app_client
    server, client = mock_delta
    viewer_token = create_access_token("human_viewer", role="VIEWER")

    daemon = ReconciliationDaemon(
        client=client,
        session_factory=db_session_factory,
        account_id="ACC_LIVE_PRIMARY",
        product_id=1234,
    )
    rec_service = ReconciliationService.get_instance()
    rec_service.register_daemon(daemon)

    # 1. /status endpoint
    res_status = await ac.get(
        "/api/v1/reconciliation/status",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert res_status.status_code == 200
    status_data = res_status.json()
    assert status_data["account_id"] == "ACC_LIVE_PRIMARY"
    assert status_data["product_symbol"] == "PAXGUSDT"
    assert status_data["discrepancy_count"] == 0
    assert status_data["status"] == "CLEAN"

    # 2. Seed snapshots and test /snapshots endpoint with pagination
    async with db_session_factory() as session:
        for i in range(5):
            snap = ReconciliationSnapshot(
                snapshot_id=f"snap-{i}",
                account_id="ACC_LIVE_PRIMARY",
                timestamp=datetime.now(timezone.utc) + timedelta(seconds=i),
                discrepancy_count=0,
                cash_local=50000.0,
                cash_exchange=50000.0,
                upnl_local=0.0,
                upnl_exchange=0.0,
                status="CLEAN",
                details="{}",
            )
            session.add(snap)
        await session.commit()

    res_snaps = await ac.get(
        "/api/v1/reconciliation/snapshots?limit=3&offset=0",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert res_snaps.status_code == 200
    snaps_data = res_snaps.json()
    assert len(snaps_data) == 3
