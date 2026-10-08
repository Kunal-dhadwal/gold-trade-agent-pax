"""Unit tests for Phase 2 research sessions, hypotheses, and agent decisions database models."""

from datetime import datetime, timezone
from uuid import uuid4
import pytest
from sqlalchemy import select

from src.database.connection import AsyncSessionLocal
from src.database.models import (
    AgentDecision,
    ResearchHypothesis,
    ResearchSession,
    Strategy,
    StrategyVersion,
)


@pytest.mark.asyncio
async def test_research_session_and_hypothesis_persistence():
    async with AsyncSessionLocal() as session:
        session_id = str(uuid4())
        now = datetime.now(timezone.utc)

        # 1. Create Research Session
        research_session = ResearchSession(
            id=session_id,
            name="Gold London Breakout Study",
            status="ACTIVE",
            target_regime="BREAKOUT",
            timeframe="15m",
            instrument="XAUUSD",
            created_at=now,
            updated_at=now,
        )
        session.add(research_session)

        # 2. Create Hypothesis under Session
        hypo_id = str(uuid4())
        hypothesis = ResearchHypothesis(
            id=hypo_id,
            session_id=session_id,
            thesis_statement="London opening range expansion leads to intraday follow-through.",
            economic_rationale="Order flow clustering at European open shifts market inventory.",
            expected_edge="1.8 profit factor with 15-pip stop loss.",
            status="FORMULATED",
            created_at=now,
            updated_at=now,
        )
        session.add(hypothesis)

        # 3. Create StrategyVersion linked to Hypothesis
        strat_id = f"test_strat_{uuid4().hex[:8]}"
        strategy = Strategy(
            id=strat_id,
            name="London Breakout Test",
            category="BREAKOUT",
            author="AGENT_3",
            created_at=now,
            updated_at=now,
        )
        session.add(strategy)

        version_id = str(uuid4())
        strat_version = StrategyVersion(
            id=version_id,
            strategy_id=strat_id,
            version_tag="v1",
            timeframe="15m",
            status="RESEARCH",
            logic_description="Breakout strategy linked to hypothesis",
            hypothesis_id=hypo_id,
            created_at=now,
            updated_at=now,
        )
        session.add(strat_version)
        await session.flush()

        # 4. Create AgentDecision record
        decision_id = str(uuid4())
        decision = AgentDecision(
            id=decision_id,
            agent_name="Agent-3-StrategyResearcher",
            input_context_summary="Evaluated London session volume spike",
            prompt_tokens=450,
            completion_tokens=220,
            reasoning_output="Proposed 15m breakout strategy with 1.5 ATR trailing stop.",
            strategy_version_id=version_id,
            created_at=now,
        )
        session.add(decision)

        await session.commit()

        # Query and verify
        res = await session.execute(
            select(ResearchSession).where(ResearchSession.id == session_id)
        )
        queried_session = res.scalar_one_or_none()
        assert queried_session is not None
        assert queried_session.name == "Gold London Breakout Study"

        hypo_res = await session.execute(
            select(ResearchHypothesis).where(ResearchHypothesis.id == hypo_id)
        )
        queried_hypo = hypo_res.scalar_one_or_none()
        assert queried_hypo is not None
        assert queried_hypo.session_id == session_id

        ver_res = await session.execute(
            select(StrategyVersion).where(StrategyVersion.id == version_id)
        )
        queried_ver = ver_res.scalar_one_or_none()
        assert queried_ver is not None
        assert queried_ver.hypothesis_id == hypo_id

        dec_res = await session.execute(
            select(AgentDecision).where(AgentDecision.id == decision_id)
        )
        queried_dec = dec_res.scalar_one_or_none()
        assert queried_dec is not None
        assert queried_dec.prompt_tokens == 450
