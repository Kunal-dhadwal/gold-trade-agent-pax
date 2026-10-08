"""Integration tests for P2-3: Verified research leaderboard querying genuine database records."""

from datetime import datetime, timezone
from uuid import uuid4
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app
from src.database.connection import AsyncSessionLocal
from src.database.models import (
    Backtest,
    Instrument,
    Strategy,
    StrategyVersion,
    ValidationResult,
)


@pytest.mark.asyncio
async def test_leaderboard_verified_database_querying():
    """Verify that leaderboard reads genuine persisted records and orders by CSS descending."""
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        # Check or create base instrument
        inst = await session.get(Instrument, "XAUUSD")
        if not inst:
            inst = Instrument(
                id="XAUUSD",
                symbol="XAUUSD",
                name="Gold Spot US Dollar",
                asset_class="COMMODITY",
                quote_currency="USD",
                tick_size=0.01,
                lot_size=100.0,
            )
            session.add(inst)
            await session.flush()

        # Strategy 1: Prime Candidate (CSS = 86.5, Tier 1)
        s1_id = f"strat_alpha_{uuid4().hex[:6]}"
        strat_1 = Strategy(id=s1_id, name="Alpha Trend", category="TREND", author="AGENT_3", created_at=now, updated_at=now)
        v1_id = str(uuid4())
        v1 = StrategyVersion(id=v1_id, strategy_id=s1_id, version_tag="v1", timeframe="1h", status="ACCEPTED_CANDIDATE", logic_description="Alpha logic", created_at=now, updated_at=now)

        # Strategy 2: Reserve Candidate (CSS = 74.2, Tier 2)
        s2_id = f"strat_beta_{uuid4().hex[:6]}"
        strat_2 = Strategy(id=s2_id, name="Beta Breakout", category="BREAKOUT", author="AGENT_3", created_at=now, updated_at=now)
        v2_id = str(uuid4())
        v2 = StrategyVersion(id=v2_id, strategy_id=s2_id, version_tag="v1", timeframe="1h", status="RESERVE_CANDIDATE", logic_description="Beta logic", created_at=now, updated_at=now)

        # Strategy 3: Disqualified Candidate (CSS = 42.0, Disqualified)
        s3_id = f"strat_gamma_{uuid4().hex[:6]}"
        strat_3 = Strategy(id=s3_id, name="Gamma MeanRev", category="REVERSION", author="AGENT_3", created_at=now, updated_at=now)
        v3_id = str(uuid4())
        v3 = StrategyVersion(id=v3_id, strategy_id=s3_id, version_tag="v1", timeframe="1h", status="REJECTED", logic_description="Gamma logic", created_at=now, updated_at=now)

        # Strategy 4: Unvalidated Strategy (Has version, but NO backtest or validation result)
        s4_id = f"strat_delta_{uuid4().hex[:6]}"
        strat_4 = Strategy(id=s4_id, name="Delta Draft", category="DRAFT", author="AGENT_3", created_at=now, updated_at=now)
        v4 = StrategyVersion(id=str(uuid4()), strategy_id=s4_id, version_tag="v1", timeframe="1h", status="RESEARCH", logic_description="Delta draft logic", created_at=now, updated_at=now)

        session.add_all([strat_1, v1, strat_2, v2, strat_3, v3, strat_4, v4])
        await session.flush()

        # Add Backtests
        b1_id = str(uuid4())
        b1 = Backtest(
            id=b1_id, strategy_version_id=v1_id, instrument_id="XAUUSD", timeframe="1h",
            start_date=now, end_date=now, final_equity=12500.0, total_net_profit=2500.0,
            total_return_pct=25.0, profit_factor=2.10, win_rate_pct=64.0, max_drawdown_pct=6.5,
            max_drawdown_usd=650.0, total_trades=75, winning_trades=48, losing_trades=27, created_at=now,
        )
        b2_id = str(uuid4())
        b2 = Backtest(
            id=b2_id, strategy_version_id=v2_id, instrument_id="XAUUSD", timeframe="1h",
            start_date=now, end_date=now, final_equity=11200.0, total_net_profit=1200.0,
            total_return_pct=12.0, profit_factor=1.55, win_rate_pct=52.0, max_drawdown_pct=11.0,
            max_drawdown_usd=1100.0, total_trades=60, winning_trades=31, losing_trades=29, created_at=now,
        )
        b3_id = str(uuid4())
        b3 = Backtest(
            id=b3_id, strategy_version_id=v3_id, instrument_id="XAUUSD", timeframe="1h",
            start_date=now, end_date=now, final_equity=9800.0, total_net_profit=-200.0,
            total_return_pct=-2.0, profit_factor=0.92, win_rate_pct=38.0, max_drawdown_pct=18.5,
            max_drawdown_usd=1850.0, total_trades=55, winning_trades=21, losing_trades=34, created_at=now,
        )
        session.add_all([b1, b2, b3])
        await session.flush()

        # Add Validation Results
        val1 = ValidationResult(
            id=str(uuid4()), backtest_id=b1_id, oos_start_date=now, oos_end_date=now,
            oos_sharpe=2.40, oos_profit_factor=1.95, oos_drawdown_pct=7.2, degradation_ratio=0.12,
            is_oos_passed=True, robustness_score=86.50, created_at=now,
        )
        val2 = ValidationResult(
            id=str(uuid4()), backtest_id=b2_id, oos_start_date=now, oos_end_date=now,
            oos_sharpe=1.60, oos_profit_factor=1.45, oos_drawdown_pct=12.5, degradation_ratio=0.22,
            is_oos_passed=True, robustness_score=74.20, created_at=now,
        )
        val3 = ValidationResult(
            id=str(uuid4()), backtest_id=b3_id, oos_start_date=now, oos_end_date=now,
            oos_sharpe=0.30, oos_profit_factor=0.85, oos_drawdown_pct=21.0, degradation_ratio=0.55,
            is_oos_passed=False, robustness_score=42.00, created_at=now,
        )
        session.add_all([val1, val2, val3])
        await session.commit()

    # Query leaderboard endpoint
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/research/leaderboard")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data) >= 3

        # Verify Unvalidated Strategy (Delta Draft) is excluded
        strat_ids = [entry["strategy_id"] for entry in data]
        assert s4_id not in strat_ids
        assert s1_id in strat_ids
        assert s2_id in strat_ids
        assert s3_id in strat_ids

        # Locate each strategy entry
        entry_1 = next(e for e in data if e["strategy_id"] == s1_id)
        entry_2 = next(e for e in data if e["strategy_id"] == s2_id)
        entry_3 = next(e for e in data if e["strategy_id"] == s3_id)

        # Verify Strategy 1 metrics (Verified actual values, NOT fabricated defaults)
        assert entry_1["css_score"] == 86.5
        assert entry_1["tier"] == "TIER_1"
        assert entry_1["profit_factor"] == 1.95
        assert entry_1["win_rate_pct"] == 64.0
        assert entry_1["max_drawdown_pct"] == 7.2

        # Verify Strategy 2 metrics
        assert entry_2["css_score"] == 74.2
        assert entry_2["tier"] == "TIER_2"
        assert entry_2["profit_factor"] == 1.45
        assert entry_2["win_rate_pct"] == 52.0
        assert entry_2["max_drawdown_pct"] == 12.5

        # Verify Strategy 3 metrics (Disqualified)
        assert entry_3["css_score"] == 42.0
        assert entry_3["tier"] == "DISQUALIFIED"
        assert entry_3["profit_factor"] == 0.85
        assert entry_3["win_rate_pct"] == 38.0
        assert entry_3["max_drawdown_pct"] == 21.0

        # Verify CSS ordering (Strictly descending)
        scores = [entry["css_score"] for entry in data]
        assert scores == sorted(scores, reverse=True)
