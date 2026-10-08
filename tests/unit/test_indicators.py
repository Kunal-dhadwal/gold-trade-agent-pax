"""Unit tests for quantitative technical indicators."""

import numpy as np
import pandas as pd
import pytest
from src.quant.indicators.moving_averages import sma, ema, wma
from src.quant.indicators.volatility import atr, bollinger_bands
from src.quant.indicators.momentum import rsi, macd
from src.quant.indicators.trend import adx, donchian_channels


@pytest.fixture
def sample_ohlcv():
    """Generate deterministic synthetic Gold OHLCV data."""
    dates = pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC")
    np.random.seed(42)
    base_price = 2000.0
    returns = np.random.normal(0.0002, 0.003, size=100)
    close = base_price * np.cumprod(1 + returns)
    high = close + np.random.uniform(1.0, 5.0, size=100)
    low = close - np.random.uniform(1.0, 5.0, size=100)
    open_p = (high + low) / 2.0
    volume = np.random.uniform(100, 1000, size=100)

    df = pd.DataFrame({
        "timestamp": dates,
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "source": "SYNTHETIC",
        "instrument": "XAUUSD",
    })
    return df


def test_moving_averages(sample_ohlcv):
    close = sample_ohlcv["close"]
    s = sma(close, 20)
    e = ema(close, 20)
    w = wma(close, 20)

    assert len(s) == len(close)
    assert len(e) == len(close)
    assert len(w) == len(close)
    assert np.isnan(s.iloc[0])
    assert not np.isnan(s.iloc[19])
    assert not np.isnan(e.iloc[19])
    assert not np.isnan(w.iloc[19])


def test_volatility_indicators(sample_ohlcv):
    atr_val = atr(sample_ohlcv, 14)
    assert len(atr_val) == len(sample_ohlcv)
    assert (atr_val.dropna() > 0).all()

    upper, middle, lower, bandwidth, percent_b = bollinger_bands(sample_ohlcv["close"], 20, 2.0)
    valid_idx = slice(20, None)
    assert (upper.iloc[valid_idx] >= middle.iloc[valid_idx]).all()
    assert (middle.iloc[valid_idx] >= lower.iloc[valid_idx]).all()
    assert (bandwidth.iloc[valid_idx] > 0).all()


def test_momentum_indicators(sample_ohlcv):
    rsi_val = rsi(sample_ohlcv["close"], 14)
    assert len(rsi_val) == len(sample_ohlcv)
    valid_rsi = rsi_val.iloc[14:]
    assert (valid_rsi >= 0.0).all()
    assert (valid_rsi <= 100.0).all()

    macd_line, signal_line, hist = macd(sample_ohlcv["close"], 12, 26, 9)
    assert len(macd_line) == len(sample_ohlcv)
    assert np.isclose(hist.iloc[-1], macd_line.iloc[-1] - signal_line.iloc[-1])


def test_trend_indicators(sample_ohlcv):
    upper, lower, mid = donchian_channels(sample_ohlcv, 20)
    valid_idx = slice(20, None)
    assert (upper.iloc[valid_idx] >= sample_ohlcv["high"].iloc[valid_idx]).all()
    assert (lower.iloc[valid_idx] <= sample_ohlcv["low"].iloc[valid_idx]).all()

    adx_val, p_di, m_di = adx(sample_ohlcv, 14)
    assert len(adx_val) == len(sample_ohlcv)
    valid_adx = adx_val.iloc[28:]
    assert (valid_adx >= 0.0).all()
