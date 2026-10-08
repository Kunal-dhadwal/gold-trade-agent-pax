"""Integration tests for Phase 2 AI Research and Validation REST API endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app


@pytest.mark.asyncio
async def test_research_session_and_hypothesis_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create research session
        sess_resp = await client.post(
            "/api/v1/research/sessions",
            json={
                "name": "Gold Asian Session Momentum Exploration",
                "target_regime": "TRENDING_BULL",
                "timeframe": "1h",
                "instrument": "XAUUSD",
                "max_iterations": 10,
            },
        )
        assert sess_resp.status_code == 200
        sess_data = sess_resp.json()
        assert sess_data["name"] == "Gold Asian Session Momentum Exploration"
        session_id = sess_data["id"]

        # 2. Get session
        get_resp = await client.get(f"/api/v1/research/sessions/{session_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == session_id

        # 3. Create hypothesis
        hypo_resp = await client.post(
            f"/api/v1/research/sessions/{session_id}/hypotheses",
            json={
                "thesis_statement": "Gold exhibits structural breakout persistence during Asian market open.",
                "economic_rationale": "Liquidity imbalance between Tokyo and Sydney creates directional inertia.",
                "expected_edge": "Average favorable excursion of 15 pips before 0.5 ATR retracement.",
            },
        )
        assert hypo_resp.status_code == 200
        hypo_data = hypo_resp.json()
        assert hypo_data["session_id"] == session_id
        assert hypo_data["status"] == "FORMULATED"


@pytest.mark.asyncio
async def test_score_calculation_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/research/strategies/score",
            json={
                "sortino_oos": 3.0,
                "max_dd_oos": 0.04,
                "gpf_oos": 2.20,
                "wfe": 0.85,
                "p95_dd_mc": 0.05,
                "degradation": 0.10,
                "passed_hard_gates": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["css_score"] >= 80.0
        assert data["tier"] == "TIER_1"
        assert data["passed_hard_gates"] is True


@pytest.mark.asyncio
async def test_leaderboard_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/research/leaderboard")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


@pytest.mark.asyncio
async def test_async_job_submission_and_polling():
    valid_proposal = {
        "name": "XAU_API_TEST",
        "hypothesis": "Valid strategy proposal to test asynchronous validation job submission.",
        "direction": "LONG",
        "instrument": "XAUUSD",
        "timeframe": "1h",
        "data_source": "DUKASCOPY",
        "target_regimes": ["TRENDING_BULL"],
        "indicators": [
            {"alias": "ema_20", "name": "EMA", "inputs": {"period": 20}},
        ],
        "entry_rules": {
            "operator": "AND",
            "conditions": [
                {
                    "left": {"type": "PRICE", "name": "close", "lag": 0},
                    "comparator": ">",
                    "right": {"type": "INDICATOR", "name": "ema_20", "lag": 0},
                }
            ],
        },
        "exit_rules": {
            "operator": "OR",
            "conditions": [
                {
                    "left": {"type": "PRICE", "name": "close", "lag": 0},
                    "comparator": "<",
                    "right": {"type": "INDICATOR", "name": "ema_20", "lag": 0},
                }
            ],
        },
        "stop_loss": {"type": "ATR_MULTIPLE", "multiplier_or_value": 1.5},
        "take_profit": {"type": "ATR_MULTIPLE", "multiplier_or_value": 3.0},
        "constraints": {"max_holding_bars": 50, "allowed_sessions": ["ALL"]},
        "rationale": "Valid proposal for async test.",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        submit_resp = await client.post(
            "/api/v1/research/strategies/walk-forward",
            json=valid_proposal,
        )
        assert submit_resp.status_code == 202
        submit_data = submit_resp.json()
        job_id = submit_data["job_id"]
        assert job_id is not None

        # Poll job status
        poll_resp = await client.get(f"/api/v1/research/jobs/{job_id}")
        assert poll_resp.status_code == 200
        poll_data = poll_resp.json()
        assert poll_data["job_id"] == job_id
        assert poll_data["status"] in ("PENDING", "RUNNING", "COMPLETED")

