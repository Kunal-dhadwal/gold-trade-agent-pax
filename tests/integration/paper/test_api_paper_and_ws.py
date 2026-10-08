import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from src.api.auth import create_access_token
from src.api.main import app


@pytest.mark.asyncio
async def test_jwt_auth_and_rbac_enforcement():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Unauthenticated request to /accounts fails with 401
        res_unauth = await ac.get("/api/v1/paper/accounts")
        assert res_unauth.status_code == 401

        # 2. VIEWER token can access read-only accounts
        viewer_token = create_access_token("human_viewer_1", role="VIEWER")
        res_viewer = await ac.get(
            "/api/v1/paper/accounts",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res_viewer.status_code == 200

        # 3. VIEWER cannot create accounts (requires OPERATOR) -> 403
        res_viewer_post = await ac.post(
            "/api/v1/paper/accounts",
            json={"account_id": "ACC_UNAUTH_POST"},
            headers={"Authorization": f"Bearer {viewer_token}"},
        )
        assert res_viewer_post.status_code == 403

        # 4. OPERATOR token can create account -> 201
        test_acc_id = f"ACC_OP_TEST_{uuid.uuid4().hex[:8]}"
        operator_token = create_access_token("human_operator_1", role="OPERATOR")
        res_op_post = await ac.post(
            "/api/v1/paper/accounts",
            json={"account_id": test_acc_id, "initial_balance": 15000.0},
            headers={"Authorization": f"Bearer {operator_token}"},
        )
        assert res_op_post.status_code == 201
        assert res_op_post.json()["account_id"] == test_acc_id
        assert res_op_post.json()["balance"] == 15000.0

        # 5. AI identity token generation must be blocked
        with pytest.raises(PermissionError):
            create_access_token("ai_subagent_runner", role="ADMIN")


@pytest.mark.asyncio
async def test_paper_portfolio_and_parity_endpoints():
    transport = ASGITransport(app=app)
    operator_token = create_access_token("human_operator_2", role="OPERATOR")
    headers = {"Authorization": f"Bearer {operator_token}"}

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Create test account
        await ac.post(
            "/api/v1/paper/accounts",
            json={"account_id": "ACC_PORT_TEST", "initial_balance": 20000.0},
            headers=headers,
        )

        # Query portfolio
        res_port = await ac.get("/api/v1/paper/accounts/ACC_PORT_TEST/portfolio", headers=headers)
        assert res_port.status_code == 200
        pdata = res_port.json()
        assert pdata["balance"] == 20000.0
        assert pdata["equity"] == 20000.0

        # Query parity report
        res_parity = await ac.get("/api/v1/paper/parity/gold_ema_trend_cross", headers=headers)
        assert res_parity.status_code == 200
        pardata = res_parity.json()
        assert "overall_status" in pardata
        assert len(pardata["dimensions"]) == 11


@pytest.mark.asyncio
async def test_kill_switch_and_prometheus_metrics():
    transport = ASGITransport(app=app)
    operator_token = create_access_token("human_operator_3", role="OPERATOR")
    admin_token = create_access_token("human_admin_1", role="ADMIN")

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Trigger kill switch
        res_ks = await ac.post(
            "/api/v1/risk/kill-switch",
            json={"operator_id": "human_operator_3", "reason": "Test emergency trigger"},
            headers={"Authorization": f"Bearer {operator_token}"},
        )
        assert res_ks.status_code == 200
        assert res_ks.json()["status"] == "KILL_SWITCH_ACTIVE"

        # Check risk status
        res_status = await ac.get(
            "/api/v1/risk/status",
            headers={"Authorization": f"Bearer {operator_token}"},
        )
        assert res_status.status_code == 200
        assert res_status.json()["kill_switch"]["active"] is True

        # Reset kill switch (Admin role)
        res_reset = await ac.post(
            "/api/v1/risk/kill-switch/reset?operator_id=human_admin_1",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res_reset.status_code == 200
        assert res_reset.json()["status"] == "NORMAL"

        # Check Prometheus exposition endpoint
        res_metrics = await ac.get("/metrics")
        assert res_metrics.status_code == 200
        assert "paper_orders_total" in res_metrics.text
        assert "paper_account_equity" in res_metrics.text


def test_websocket_unauthenticated_rejected():
    """P2-1 / P2-4: Unauthenticated connection rejected with WS 1008 policy violation."""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/v1/paper/stream"):
                pass
        assert exc.value.code == 1008


def test_websocket_invalid_token_rejected():
    """P2-1 / P2-4: Invalid JWT token rejected with WS 1008 policy violation."""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/v1/paper/stream?token=invalid.jwt.signature"):
                pass
        assert exc.value.code == 1008


def test_websocket_authenticated_session_and_events():
    """P1-4 / P2-4: Authenticated WebSocket session receives welcome, ping/pong, and EventBus broadcasts."""
    from starlette.testclient import TestClient
    from src.api.websockets.gateway import gateway_manager
    from src.real_time.event_bus import EventEnvelope

    token = create_access_token("human_ws_operator", role="OPERATOR")

    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/v1/paper/stream?token={token}") as ws:
            # 1. Welcome handshake message
            welcome = ws.receive_json()
            assert welcome["type"] == "CONNECTION_ESTABLISHED"
            assert welcome["user_id"] == "human_ws_operator"
            assert welcome["role"] == "OPERATOR"
            assert "equity" in welcome["subscriptions"]
            assert "orders" in welcome["subscriptions"]

            # 2. Ping-pong roundtrip
            ws.send_json({"action": "PING"})
            pong = ws.receive_json()
            assert pong["type"] == "PONG"

            # 3. Custom topic subscription
            ws.send_json({"action": "SUBSCRIBE", "topic": "custom_audit_feed"})
            sub = ws.receive_json()
            assert sub["type"] == "SUBSCRIBED"
            assert sub["topic"] == "custom_audit_feed"

            # 4. EventBus event publication routed to WebSocket stream
            order_envelope = EventEnvelope(
                event_type="ORDER_FILLED",
                aggregate_id="ACC_WS_TEST",
                idempotency_key="EVT-ORDER-1",
                payload={"order_id": "ORD-WS-99", "symbol": "XAUUSD", "qty": 1.5, "price": 2045.50},
            )
            ws.portal.call(gateway_manager.event_bus.publish, "order.filled", order_envelope)
            ws_order_event = ws.receive_json()
            assert ws_order_event["type"] == "ORDER_FILLED"
            assert ws_order_event["topic"] == "orders"
            assert ws_order_event["payload"]["order_id"] == "ORD-WS-99"

            # 5. Risk alert event publication routed to alerts topic
            risk_envelope = EventEnvelope(
                event_type="RISK_LIMIT_BREACH",
                aggregate_id="ACC_WS_TEST",
                idempotency_key="EVT-RISK-1",
                payload={"level": "CRITICAL", "message": "Max drawdown threshold warning"},
            )
            ws.portal.call(gateway_manager.event_bus.publish, "risk.warning", risk_envelope)
            ws_risk_event = ws.receive_json()
            assert ws_risk_event["type"] == "RISK_LIMIT_BREACH"
            assert ws_risk_event["topic"] == "alerts"
            assert ws_risk_event["payload"]["level"] == "CRITICAL"

