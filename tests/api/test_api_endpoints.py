"""Integration and API route tests."""

from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient
from src.api.main import app
from src.core.config import settings
from src.data.storage.parquet_manager import ParquetStorageManager


@pytest.fixture(autouse=True)
def seed_test_data(tmp_path):
    """Seed test dataset for backtesting endpoint tests."""
    mgr = ParquetStorageManager(base_dir=settings.DATA_DIR)
    dates = pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC")
    np.random.seed(42)
    close = 2000.0 + np.cumsum(np.random.normal(0.2, 2.0, 100))
    df = pd.DataFrame({
        "timestamp": dates,
        "open": close - 0.5,
        "high": close + 3.0,
        "low": close - 3.0,
        "close": close,
        "volume": np.full(100, 500.0),
        "source": "TEST",
        "instrument": "GC=F",
    })
    mgr.save_candles(df, "GC=F", "1h", "TEST")


@pytest.mark.asyncio
async def test_health_and_system_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "OK"
        assert "PHASE 1" in data["phase"]
        assert data["live_trading_enabled"] is False

        info = await ac.get("/api/v1/system/info")
        assert info.status_code == 200
        assert "XAUUSD" in info.json()["instruments"]


@pytest.mark.asyncio
async def test_strategy_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/strategies")
        assert res.status_code == 200
        strats = res.json()
        assert len(strats) >= 3

        detail = await ac.get("/api/v1/strategies/gold_ema_trend_cross")
        assert detail.status_code == 200
        assert detail.json()["strategy_id"] == "gold_ema_trend_cross"


@pytest.mark.asyncio
async def test_backtest_run_and_retrieval_flow():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        req_body = {
            "strategy_id": "gold_ema_trend_cross",
            "instrument": "GC=F",
            "timeframe": "1h",
            "initial_capital": 10000.0,
            "risk_per_trade_pct": 1.0,
            "parameters": {
                "fast_ema": 10,
                "slow_ema": 20,
                "trend_ema": 50,
            },
        }
        run_res = await ac.post("/api/v1/backtest/run", json=req_body)
        assert run_res.status_code == 200
        run_data = run_res.json()
        assert "backtest_id" in run_data
        assert "metrics" in run_data
        bt_id = run_data["backtest_id"]

        # Retrieve saved backtest
        get_res = await ac.get(f"/api/v1/backtest/{bt_id}")
        assert get_res.status_code == 200
        get_data = get_res.json()
        assert get_data["backtest_id"] == bt_id
        assert get_data["instrument"] == "GC=F"


@pytest.mark.asyncio
async def test_data_routes():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Status endpoint
        status_res = await ac.get("/api/v1/data/status")
        assert status_res.status_code == 200
        assert "datasets" in status_res.json()

        # 2. Quality reports endpoint
        qr_res = await ac.get("/api/v1/data/quality-reports")
        assert qr_res.status_code == 200
        assert "reports" in qr_res.json()

        # 3. Candles retrieval (existing seeded dataset)
        c_res = await ac.get("/api/v1/data/candles?instrument=GC=F&timeframe=1h&limit=50")
        assert c_res.status_code == 200
        data = c_res.json()
        assert data["total_bars"] == 50
        assert len(data["candles"]) == 50
        assert data["candles"][0]["regime"] is not None

        # 4. Candles 404 for nonexistent instrument
        c_404 = await ac.get("/api/v1/data/candles?instrument=NONEXISTENT&timeframe=1h")
        assert c_404.status_code == 404


@pytest.mark.asyncio
async def test_api_error_handling():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Backtest 404 when dataset doesn't exist
        res1 = await ac.post("/api/v1/backtest/run", json={
            "strategy_id": "gold_ema_trend_cross",
            "instrument": "NONEXISTENT",
            "timeframe": "1h",
        })
        assert res1.status_code == 404

        # 2. Backtest 404 when strategy doesn't exist
        res2 = await ac.post("/api/v1/backtest/run", json={
            "strategy_id": "nonexistent_strat",
            "instrument": "GC=F",
            "timeframe": "1h",
        })
        assert res2.status_code == 404

        # 3. Backtest 400 when parameters are invalid
        res3 = await ac.post("/api/v1/backtest/run", json={
            "strategy_id": "gold_ema_trend_cross",
            "instrument": "GC=F",
            "timeframe": "1h",
            "parameters": {"fast_ema": 1},  # min is 5
        })
        assert res3.status_code == 400

        # 4. Backtest ID 404
        res4 = await ac.get("/api/v1/backtest/nonexistent-id")
        assert res4.status_code == 404

        # 5. Strategy ID 404
        res5 = await ac.get("/api/v1/strategies/nonexistent_strategy")
        assert res5.status_code == 404


@pytest.mark.asyncio
async def test_data_ingest_route():
    from unittest.mock import AsyncMock, patch

    transport = ASGITransport(app=app)
    dates = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
    mock_df = pd.DataFrame({
        "timestamp": dates,
        "open": np.full(10, 2000.0),
        "high": np.full(10, 2010.0),
        "low": np.full(10, 1990.0),
        "close": np.full(10, 2005.0),
        "volume": np.full(10, 100.0),
        "source": "YAHOO",
        "instrument": "GC=F",
    })
    with patch("src.data.sources.yahoo.YahooFinanceClient.fetch_candles", new_callable=AsyncMock, return_value=mock_df):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post("/api/v1/data/ingest", json={
                "symbol": "GC=F",
                "source": "YAHOO",
                "timeframe": "1h",
            })
            assert res.status_code == 200
            data = res.json()
            assert data["success"] is True
            assert data["bars_ingested"] == 10
            assert data["instrument"] == "GC=F"


