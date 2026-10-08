"""Unit tests for Yahoo Finance data connector."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
import pandas as pd
import pytest
from src.data.sources.yahoo import YahooFinanceClient


def make_resp(data, status_code=200):
    return httpx.Response(
        status_code=status_code,
        json=data,
        request=httpx.Request("GET", "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"),
    )


@pytest.mark.asyncio
async def test_yahoo_fetch_candles_success(tmp_path):
    client = YahooFinanceClient(raw_storage_dir=tmp_path)

    mock_yahoo_payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1704067200, 1704070800],
                    "indicators": {
                        "quote": [
                            {
                                "open": [2060.0, 2062.5],
                                "high": [2065.0, 2070.0],
                                "low": [2058.0, 2061.0],
                                "close": [2062.5, 2068.0],
                                "volume": [1000.0, 1200.0],
                            }
                        ]
                    },
                }
            ]
        }
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=make_resp(mock_yahoo_payload)):
        df = await client.fetch_candles(
            symbol="GC=F",
            timeframe="1h",
            start_time=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 1, 2, 0, tzinfo=timezone.utc),
        )

        assert len(df) == 2
        assert df["close"].iloc[0] == 2062.5
        assert df["instrument"].iloc[0] == "GC=F"
        assert df["source"].iloc[0] == "YAHOO"

        # Verify raw persistence
        raw_files = list(tmp_path.glob("raw_GC_F_*.json"))
        assert len(raw_files) == 1


@pytest.mark.asyncio
async def test_yahoo_fetch_candles_empty_error(tmp_path):
    client = YahooFinanceClient(raw_storage_dir=tmp_path)

    empty_payload = {"chart": {"result": None, "error": "Not Found"}}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=make_resp(empty_payload)):
        with pytest.raises(ValueError, match="Yahoo Finance returned no data"):
            await client.fetch_candles(symbol="GC=F", timeframe="1h")


@pytest.mark.asyncio
async def test_yahoo_fetch_candles_missing_bars(tmp_path):
    client = YahooFinanceClient(raw_storage_dir=tmp_path)

    # All None bars
    all_none_payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1704067200],
                    "indicators": {
                        "quote": [
                            {
                                "open": [None],
                                "high": [None],
                                "low": [None],
                                "close": [None],
                                "volume": [None],
                            }
                        ]
                    },
                }
            ]
        }
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=make_resp(all_none_payload)):
        with pytest.raises(ValueError, match="No valid OHLCV candles"):
            await client.fetch_candles(symbol="GC=F", timeframe="1h")

