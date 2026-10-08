"""Unit tests for P2-1: Dynamic instrument and timeframe binding in API routes and pipeline."""

from unittest.mock import MagicMock, patch
import pytest
from pydantic import ValidationError

from src.api.routes.research import (
    get_or_create_research_data,
    validate_strategy_synchronous,
    submit_walk_forward_job,
    submit_monte_carlo_job,
)
from src.strategy.contracts import (
    ConditionComparison,
    ConditionNode,
    DataSourceEnum,
    ExecutionConstraints,
    IndicatorNameEnum,
    IndicatorSpec,
    InstrumentEnum,
    RegimeEnum,
    StopLossSpec,
    StrategyProposal,
    TakeProfitSpec,
    TimeframeEnum,
    TradeDirection,
    ValueRef,
    ValueRefType,
)


def make_proposal_dict(instrument: str, timeframe: str) -> dict:
    """Build a valid StrategyProposal dictionary for API endpoints."""
    return {
        "name": "XAU_BINDING_TEST",
        "hypothesis": "Dynamic instrument and timeframe binding verification proposal.",
        "direction": "LONG",
        "instrument": instrument,
        "timeframe": timeframe,
        "data_source": "DUKASCOPY",
        "target_regimes": ["RANGING"],
        "indicators": [
            {"alias": "ema_20", "name": "EMA", "inputs": {"period": 20}}
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
        "rationale": "Testing dynamic parameter binding.",
    }


# ==============================================================================
# CASE A: XAUUSD + 1h
# ==============================================================================
@pytest.mark.asyncio
async def test_binding_case_a_xauusd_1h():
    proposal_dict = make_proposal_dict(instrument="XAUUSD", timeframe="1h")
    with patch("src.api.routes.research.get_or_create_research_data") as mock_data:
        mock_df = get_or_create_research_data("XAUUSD", "1h")
        mock_data.return_value = mock_df

        res = await validate_strategy_synchronous(proposal_dict)
        mock_data.assert_called_once_with(instrument="XAUUSD", timeframe="1h")
        assert res["strategy_name"] == "XAU_BINDING_TEST"


# ==============================================================================
# CASE B: XAUUSD + 15m
# ==============================================================================
@pytest.mark.asyncio
async def test_binding_case_b_xauusd_15m():
    proposal_dict = make_proposal_dict(instrument="XAUUSD", timeframe="15m")
    with patch("src.api.routes.research.get_or_create_research_data") as mock_data:
        mock_df = get_or_create_research_data("XAUUSD", "15m")
        mock_data.return_value = mock_df

        res = await validate_strategy_synchronous(proposal_dict)
        mock_data.assert_called_once_with(instrument="XAUUSD", timeframe="15m")
        assert res["strategy_name"] == "XAU_BINDING_TEST"


# ==============================================================================
# CASE C: GC=F + 1h
# ==============================================================================
@pytest.mark.asyncio
async def test_binding_case_c_gcf_1h():
    proposal_dict = make_proposal_dict(instrument="GC=F", timeframe="1h")
    with patch("src.api.routes.research.get_or_create_research_data") as mock_data:
        mock_df = get_or_create_research_data("GC=F", "1h")
        mock_data.return_value = mock_df

        res = await validate_strategy_synchronous(proposal_dict)
        mock_data.assert_called_once_with(instrument="GC=F", timeframe="1h")
        assert res["strategy_name"] == "XAU_BINDING_TEST"


# ==============================================================================
# CASE D: PAXGUSDT + 5m
# ==============================================================================
@pytest.mark.asyncio
async def test_binding_case_d_paxgusdt_5m():
    proposal_dict = make_proposal_dict(instrument="PAXGUSDT", timeframe="5m")
    with patch("src.api.routes.research.get_or_create_research_data") as mock_data:
        mock_df = get_or_create_research_data("PAXGUSDT", "5m")
        mock_data.return_value = mock_df

        res = await validate_strategy_synchronous(proposal_dict)
        mock_data.assert_called_once_with(instrument="PAXGUSDT", timeframe="5m")
        assert res["strategy_name"] == "XAU_BINDING_TEST"


# ==============================================================================
# ASYNC JOB SUBMISSIONS: WFA & MONTE CARLO
# ==============================================================================
@pytest.mark.asyncio
async def test_wfa_and_mc_job_binding():
    proposal_dict = make_proposal_dict(instrument="GC=F", timeframe="15m")
    mock_bg = MagicMock()

    with patch("src.api.routes.research.get_or_create_research_data") as mock_data:
        mock_df = get_or_create_research_data("GC=F", "15m")
        mock_data.return_value = mock_df

        # WFA submission
        wfa_resp = await submit_walk_forward_job(proposal_dict, background_tasks=mock_bg)
        mock_data.assert_called_with(instrument="GC=F", timeframe="15m")
        assert wfa_resp.job_id is not None

        mock_data.reset_mock()

        # Monte Carlo submission
        mc_resp = await submit_monte_carlo_job(proposal_dict, background_tasks=mock_bg)
        mock_data.assert_called_with(instrument="GC=F", timeframe="15m")
        assert mc_resp.job_id is not None


# ==============================================================================
# VALIDATION REJECTION: UNSUPPORTED INSTRUMENT / TIMEFRAME CANNOT BYPASS
# ==============================================================================
def test_unsupported_instrument_rejected_by_schema():
    """Unsupported instrument cannot bypass Pydantic validation."""
    invalid_dict = make_proposal_dict(instrument="BTCUSDT", timeframe="1h")
    with pytest.raises(ValidationError) as exc:
        StrategyProposal.model_validate(invalid_dict)
    assert "Input should be 'XAUUSD', 'GC=F' or 'PAXGUSDT'" in str(exc.value)


def test_unsupported_timeframe_rejected_by_schema():
    """Unsupported timeframe cannot bypass Pydantic validation."""
    invalid_dict = make_proposal_dict(instrument="XAUUSD", timeframe="2h")
    with pytest.raises(ValidationError) as exc:
        StrategyProposal.model_validate(invalid_dict)
    assert "Input should be '1m', '5m', '15m', '30m', '1h', '4h' or '1d'" in str(exc.value)

