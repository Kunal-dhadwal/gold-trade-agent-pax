"""Unit tests for MarketRegimeDetector and Gold MarketSession identification."""

from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest
from src.core.constants import MarketRegimeType, MarketSession
from src.quant.regime.detector import MarketRegimeDetector
from src.quant.sessions import get_market_session, tag_sessions


def test_market_session_tagging():
    # Test specific hours
    assert get_market_session(datetime(2024, 1, 1, 3, 0, tzinfo=timezone.utc)) == MarketSession.ASIAN
    assert get_market_session(datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc)) == MarketSession.LONDON
    assert get_market_session(datetime(2024, 1, 1, 14, 0, tzinfo=timezone.utc)) == MarketSession.LONDON_NY_OVERLAP
    assert get_market_session(datetime(2024, 1, 1, 18, 0, tzinfo=timezone.utc)) == MarketSession.NEW_YORK
    assert get_market_session(datetime(2024, 1, 1, 22, 0, tzinfo=timezone.utc)) == MarketSession.OFF_HOURS

    # Test tag_sessions DataFrame
    dates = [
        datetime(2024, 1, 1, 3, 0, tzinfo=timezone.utc),
        datetime(2024, 1, 1, 14, 0, tzinfo=timezone.utc),
    ]
    df = pd.DataFrame({"timestamp": dates, "close": [2000.0, 2010.0]})
    tags = tag_sessions(df)
    assert tags.iloc[0] == MarketSession.ASIAN.value
    assert tags.iloc[1] == MarketSession.LONDON_NY_OVERLAP.value

    # Empty df
    assert tag_sessions(pd.DataFrame()).empty


def test_regime_detector_short_series():
    # Series with < 30 bars should default to RANGING
    dates = pd.date_range("2024-01-01", periods=20, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "timestamp": dates,
        "open": np.full(20, 2000.0),
        "high": np.full(20, 2005.0),
        "low": np.full(20, 1995.0),
        "close": np.full(20, 2000.0),
        "volume": np.full(20, 100.0),
    })
    classified = MarketRegimeDetector.classify_series(df)
    assert "regime" in classified.columns
    assert (classified["regime"] == MarketRegimeType.RANGING.value).all()


def test_regime_detector_trending_up():
    dates = pd.date_range("2024-01-01", periods=120, freq="1h", tz="UTC")
    # Strong sustained upward trend with low noise
    close = np.linspace(2000.0, 2300.0, 120)
    df = pd.DataFrame({
        "timestamp": dates,
        "open": close - 1.0,
        "high": close + 2.0,
        "low": close - 2.0,
        "close": close,
        "volume": np.full(120, 100.0),
    })

    snapshot = MarketRegimeDetector.get_latest_regime(df)
    assert snapshot.trend_direction == "BULLISH"
    assert snapshot.adx > 20.0
    assert snapshot.confidence > 50.0


def test_regime_detector_abnormal_bar():
    dates = pd.date_range("2024-01-01", periods=50, freq="1h", tz="UTC")
    close = np.full(50, 2000.0)
    open_p = np.full(50, 2000.0)
    high = np.full(50, 2005.0)
    low = np.full(50, 1995.0)

    # 40th bar has a massive sudden 5% jump
    open_p[40] = 2000.0
    close[40] = 2120.0  # 6% move
    high[40] = 2125.0
    low[40] = 1998.0

    df = pd.DataFrame({
        "timestamp": dates,
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.full(50, 100.0),
    })

    classified = MarketRegimeDetector.classify_series(df)
    assert classified["regime"].iloc[40] == MarketRegimeType.ABNORMAL.value

