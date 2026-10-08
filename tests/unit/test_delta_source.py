"""Unit tests for Delta Exchange data source connector."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
import pandas as pd
import pytest
from src.data.sources.delta import DeltaExchangeClient


def make_resp(data, status_code=200):
    return httpx.Response(
        status_code=status_code,
        json=data,
        request=httpx.Request("GET", "https://api.delta.exchange"),
    )


@pytest.mark.asyncio
async def test_delta_get_products_and_gold_filter(tmp_path):
    client = DeltaExchangeClient(raw_storage_dir=tmp_path)

    mock_products = {
        "result": [
            {"id": 1, "symbol": "BTCUSD", "contract_type": "perpetual_futures"},
            {"id": 2, "symbol": "XAUUSD", "contract_type": "perpetual_futures"},
            {"id": 3, "symbol": "PAXGUSDT", "contract_type": "perpetual_futures"},
            {"id": 4, "symbol": "GOLD_FUTURE", "contract_type": "futures"},
        ]
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=make_resp(mock_products)):
        products = await client.get_products()
        assert len(products) == 4

        gold_products = await client.get_gold_products()
        assert len(gold_products) == 2
        symbols = [p["symbol"] for p in gold_products]
        assert "XAUUSD" in symbols
        assert "GOLD_FUTURE" in symbols


@pytest.mark.asyncio
async def test_delta_fetch_candles_dict_format(tmp_path):
    client = DeltaExchangeClient(raw_storage_dir=tmp_path)

    mock_candles_payload = {
        "result": [
            {
                "time": 1704067200,  # 2024-01-01 00:00:00 UTC
                "open": 2060.0,
                "high": 2065.0,
                "low": 2058.0,
                "close": 2062.5,
                "volume": 120.0,
            },
            {
                "time": 1704070800,  # 2024-01-01 01:00:00 UTC
                "open": 2062.5,
                "high": 2070.0,
                "low": 2061.0,
                "close": 2068.0,
                "volume": 150.0,
            },
        ]
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=make_resp(mock_candles_payload)):
        df = await client.fetch_candles(
            symbol="XAUUSD",
            timeframe="1h",
            start_time=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 1, 2, 0, tzinfo=timezone.utc),
        )

        assert len(df) == 2
        assert "open" in df.columns
        assert df["close"].iloc[0] == 2062.5
        assert df["source"].iloc[0] == "DELTA_EXCHANGE"
        assert df["instrument"].iloc[0] == "XAUUSD"

        # Verify raw file was written
        raw_files = list(tmp_path.glob("raw_delta_*.json"))
        assert len(raw_files) == 1


@pytest.mark.asyncio
async def test_delta_fetch_candles_list_format(tmp_path):
    client = DeltaExchangeClient(raw_storage_dir=tmp_path)

    mock_candles_payload = {
        "result": [
            [1704067200, 2060.0, 2065.0, 2058.0, 2062.5, 120.0],
            [1704070800, 2062.5, 2070.0, 2061.0, 2068.0, 150.0],
        ]
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=make_resp(mock_candles_payload)):
        df = await client.fetch_candles(symbol="XAUUSD", timeframe="1h")
        assert len(df) == 2
        assert df["close"].iloc[1] == 2068.0


@pytest.mark.asyncio
async def test_delta_fetch_candles_empty(tmp_path):
    client = DeltaExchangeClient(raw_storage_dir=tmp_path)

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=make_resp({"result": []})):
        df = await client.fetch_candles(symbol="XAUUSD", timeframe="1h")
        assert df.empty
        assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume", "source", "instrument"]

