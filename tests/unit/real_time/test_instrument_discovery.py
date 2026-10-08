"""Unit tests for Delta Exchange public product discovery and fail-closed validation."""

import json
from unittest.mock import MagicMock, patch
import pytest

from src.real_time.market_data import DeltaWebSocketMarketData, SimulatedLiveMarketData


@pytest.mark.asyncio
async def test_validate_public_product_success():
    """Valid product with active status succeeds."""
    market_data = DeltaWebSocketMarketData(symbol="PAXGUSDT")
    mock_payload = {
        "result": {
            "symbol": "PAXGUSDT",
            "trading_status": "operational",
            "contract_type": "spot",
            "tick_size": 0.01,
        }
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        res = await market_data.validate_public_product()
        assert res["symbol"] == "PAXGUSDT"
        assert res["trading_status"] == "operational"


@pytest.mark.asyncio
async def test_validate_public_product_halted_fails_closed():
    """Halted trading status must fail closed with ValueError."""
    market_data = DeltaWebSocketMarketData(symbol="PAXGUSDT")
    mock_payload = {
        "result": {
            "symbol": "PAXGUSDT",
            "trading_status": "halted",
            "contract_type": "spot",
            "tick_size": 0.01,
        }
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with pytest.raises(ValueError, match="trading status is 'halted'. Fail-closed enforced."):
            await market_data.validate_public_product()


@pytest.mark.asyncio
async def test_validate_public_product_missing_product_fails_closed():
    """Missing or 404 product registry response fails closed."""
    market_data = DeltaWebSocketMarketData(symbol="PAXGUSDT")
    mock_payload = {"result": None}

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with pytest.raises(ValueError, match="not found in product registry"):
            await market_data.validate_public_product()


@pytest.mark.asyncio
async def test_validate_public_product_symbol_mismatch_fails_closed():
    """Symbol mismatch between requested and received fails closed."""
    market_data = DeltaWebSocketMarketData(symbol="PAXGUSDT")
    mock_payload = {
        "result": {
            "symbol": "XAUUSD_PERP",
            "trading_status": "operational",
            "contract_type": "perpetual_futures",
        }
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with pytest.raises(ValueError, match="Product symbol mismatch"):
            await market_data.validate_public_product()


@pytest.mark.asyncio
async def test_validate_public_product_malformed_metadata_fails_closed():
    """Missing required metadata fields fails closed."""
    market_data = DeltaWebSocketMarketData(symbol="PAXGUSDT")
    mock_payload = {
        "result": {
            "symbol": "PAXGUSDT",
            # missing trading_status and contract_type
        }
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with pytest.raises(ValueError, match="missing required field"):
            await market_data.validate_public_product()


@pytest.mark.asyncio
async def test_market_data_start_invokes_validation_and_aborts_on_failure():
    """Calling start() must validate product and fail before connecting if validation fails."""
    market_data = DeltaWebSocketMarketData(symbol="PAXGUSDT")
    with patch.object(market_data, "validate_public_product", side_effect=RuntimeError("Discovery failed")):
        with pytest.raises(RuntimeError, match="Discovery failed"):
            await market_data.start()
    assert market_data._running is False


@pytest.mark.asyncio
async def test_simulated_market_data_validation():
    """Simulated market data returns valid simulated product contract."""
    sim = SimulatedLiveMarketData(candles=[], symbol="PAXGUSDT")
    res = await sim.validate_public_product()
    assert res["symbol"] == "PAXGUSDT"
    assert res["trading_status"] == "operational"

