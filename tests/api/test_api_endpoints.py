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
