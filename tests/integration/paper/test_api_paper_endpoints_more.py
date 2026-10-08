"""Comprehensive REST and WebSocket integration tests for Phase 3 API endpoints."""

import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from src.api.auth import create_access_token
from src.api.main import app
from src.paper.service import PaperTradingService


@pytest.mark.asyncio
async def test_paper_strategy_and_account_detail_endpoints():
    transport = ASGITransport(app=app)
    operator_token = create_access_token("human_op_more_1", role="OPERATOR")
    admin_token = create_access_token("human_admin_more_1", role="ADMIN")
    headers_op = {"Authorization": f"Bearer {operator_token}"}
    headers_adm = {"Authorization": f"Bearer {admin_token}"}

    acc_id = f"ACC_MORE_{uuid.uuid4().hex[:8]}"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Create account
        acc_res = await ac.post(
            "/api/v1/paper/accounts",
            json={"account_id": acc_id, "initial_balance": 25000.0},
            headers=headers_op,
        )
        assert acc_res.status_code == 201

        # Get account detail
        det_res = await ac.get(f"/api/v1/paper/accounts/{acc_id}", headers=headers_op)
        assert det_res.status_code == 200
        assert det_res.json()["account_id"] == acc_id
        assert det_res.json()["balance"] == 25000.0

        # Equity history
        eq_res = await ac.get(f"/api/v1/paper/accounts/{acc_id}/equity-history?range=7d", headers=headers_op)
        assert eq_res.status_code == 200
        assert len(eq_res.json()) > 0

        # Strategies list
        strats_res = await ac.get("/api/v1/paper/strategies", headers=headers_op)
        assert strats_res.status_code == 200
        assert len(strats_res.json()) >= 1

        # Start strategy
        start_res = await ac.post(
            "/api/v1/paper/strategies/gold_ema_trend_cross/start",
            json={"strategy_id": "gold_ema_trend_cross", "account_id": acc_id},
            headers=headers_op,
        )
        assert start_res.status_code == 200
        assert start_res.json()["status"] == "RUNNING"

        # Pause strategy
        pause_res = await ac.post(
            f"/api/v1/paper/strategies/gold_ema_trend_cross/pause?account_id={acc_id}",
            headers=headers_op,
        )
        assert pause_res.status_code == 200
        assert pause_res.json()["status"] == "PAUSED"

        # Stop strategy
        stop_res = await ac.post(
            f"/api/v1/paper/strategies/gold_ema_trend_cross/stop?account_id={acc_id}",
            headers=headers_op,
        )
        assert stop_res.status_code == 200
        assert stop_res.json()["status"] == "STOPPED"

        # Query orders, positions, trades
        ord_res = await ac.get(f"/api/v1/paper/orders?account_id={acc_id}", headers=headers_op)
        assert ord_res.status_code == 200
        pos_res = await ac.get(f"/api/v1/paper/positions?account_id={acc_id}", headers=headers_op)
        assert pos_res.status_code == 200
        tr_res = await ac.get("/api/v1/paper/trades?strategy_id=gold_ema_trend_cross", headers=headers_op)
        assert tr_res.status_code == 200

        # Promotion evaluation
        eval_res = await ac.post(
            "/api/v1/paper/promotion/gold_ema_trend_cross/evaluate",
            headers=headers_op,
        )
        assert eval_res.status_code == 200
        assert "eligible_for_live_candidate" in eval_res.json()

        # Promotion sign-off: Unregistered operator credentials rejected (403)
        unauth_sign_res = await ac.post(
            "/api/v1/paper/promotion/gold_ema_trend_cross/sign-off",
            json={"operator_id": "human_unregistered", "operator_secret": "wrong_key"},
            headers=headers_adm,
        )
        assert unauth_sign_res.status_code == 403

        # Promotion sign-off: Valid registered operator rejected if ineligible (400)
        sign_res = await ac.post(
            "/api/v1/paper/promotion/gold_ema_trend_cross/sign-off",
            json={"operator_id": "human_admin_more_1", "operator_secret": "secure_secret_key_123"},
            headers=headers_adm,
        )
        assert sign_res.status_code == 400
        assert "NOT eligible for promotion" in sign_res.json()["detail"]

