"""Deterministic market regime classification engine for Gold."""

from dataclasses import dataclass
from typing import Dict, List, Optional
import numpy as np
import pandas as pd
from src.core.constants import MarketRegimeType
from src.quant.indicators.moving_averages import ema
from src.quant.indicators.momentum import rsi
from src.quant.indicators.trend import adx, donchian_channels
from src.quant.indicators.volatility import atr, bollinger_bands


@dataclass
class RegimeSnapshot:
    regime: MarketRegimeType
    confidence: float
    adx: float
    atr_pct: float
    bb_width: float
    rsi: float
    trend_direction: str  # "BULLISH", "BEARISH", "NEUTRAL"


class MarketRegimeDetector:
    """Classifies Gold market regimes deterministically using quantitative metrics."""

    @classmethod
    def classify_series(cls, df: pd.DataFrame) -> pd.DataFrame:
        """Add regime classifications to each bar in the DataFrame."""
        if len(df) < 30:
            res = df.copy()
            res["regime"] = MarketRegimeType.RANGING.value
            res["regime_confidence"] = 50.0
            return res

        work_df = df.copy()

        # Indicators
        atr_14 = atr(work_df, 14)
        atr_pct = (atr_14 / work_df["close"]) * 100.0
        adx_val, plus_di, minus_di = adx(work_df, 14)
        _, _, _, bb_width, _ = bollinger_bands(work_df["close"], 20, 2.0)
        ema_20 = ema(work_df["close"], 20)
        ema_50 = ema(work_df["close"], 50)
        rsi_val = rsi(work_df["close"], 14)
        don_upper, don_lower, _ = donchian_channels(work_df, 20)

        # Volatility thresholds (rolling 100-bar quantiles)
        high_vol_thresh = atr_pct.rolling(window=100, min_periods=30).quantile(0.85).fillna(atr_pct.quantile(0.85))
        low_vol_thresh = atr_pct.rolling(window=100, min_periods=30).quantile(0.15).fillna(atr_pct.quantile(0.15))

        regimes: List[str] = []
        confidences: List[float] = []

        for i in range(len(work_df)):
            close_i = work_df["close"].iloc[i]
            open_i = work_df["open"].iloc[i]
            adx_i = adx_val.iloc[i]
            p_di = plus_di.iloc[i]
            m_di = minus_di.iloc[i]
            atr_pct_i = atr_pct.iloc[i]
            high_vol_i = high_vol_thresh.iloc[i]
            low_vol_i = low_vol_thresh.iloc[i]
            e20 = ema_20.iloc[i]
            e50 = ema_50.iloc[i]
            rsi_i = rsi_val.iloc[i]
            don_up = don_upper.iloc[i]
            don_dn = don_lower.iloc[i]

            # 1. Abnormal Check (single bar jump > 2.0% or 6x ATR)
            bar_change = abs(close_i - open_i) / max(open_i, 1.0)
            if bar_change > 0.025:
                regimes.append(MarketRegimeType.ABNORMAL.value)
                confidences.append(90.0)
                continue

            # 2. Breakout Check (closing outside previous Donchian channel or 20-bar high with ADX rising)
            if i > 0 and (close_i >= don_up or close_i <= don_dn) and adx_i > 22:
                regimes.append(MarketRegimeType.BREAKOUT.value)
                confidences.append(85.0)
                continue

            # 3. High Volatility Check
            if atr_pct_i > high_vol_i and adx_i < 20:
                regimes.append(MarketRegimeType.HIGH_VOLATILITY.value)
                confidences.append(80.0)
                continue

            # 4. Low Volatility / Squeeze
            if atr_pct_i < low_vol_i and adx_i < 18:
                regimes.append(MarketRegimeType.LOW_VOLATILITY.value)
                confidences.append(80.0)
                continue

            # 5. Reversal Check (RSI extreme divergence)
            if (rsi_i > 75 and close_i < open_i) or (rsi_i < 25 and close_i > open_i):
                regimes.append(MarketRegimeType.REVERSAL.value)
                confidences.append(75.0)
                continue

            # 6. Trending Check (ADX > 25 and clean EMA order)
            if adx_i >= 25:
                if e20 > e50 and p_di > m_di:
                    regimes.append(MarketRegimeType.TRENDING_UP.value)
                    confidences.append(min(95.0, 60.0 + (adx_i - 25) * 1.5))
                elif e20 < e50 and m_di > p_di:
                    regimes.append(MarketRegimeType.TRENDING_DOWN.value)
                    confidences.append(min(95.0, 60.0 + (adx_i - 25) * 1.5))
                else:
                    regimes.append(MarketRegimeType.TRENDING_UP.value if close_i > e50 else MarketRegimeType.TRENDING_DOWN.value)
                    confidences.append(70.0)
                continue

            # 7. Default: Ranging
            regimes.append(MarketRegimeType.RANGING.value)
            confidences.append(70.0)

        work_df["regime"] = regimes
        work_df["regime_confidence"] = confidences
        return work_df

    @classmethod
    def get_latest_regime(cls, df: pd.DataFrame) -> RegimeSnapshot:
        """Classify and return snapshot for the latest bar in DataFrame."""
        classified = cls.classify_series(df)
        last_row = classified.iloc[-1]

        regime_enum = MarketRegimeType(last_row["regime"])
        adx_val, plus_di, minus_di = adx(df, 14)
        atr_14 = atr(df, 14)
        atr_pct = (atr_14.iloc[-1] / last_row["close"]) * 100.0
        _, _, _, bb_width, _ = bollinger_bands(df["close"], 20)
        rsi_val = rsi(df["close"], 14)

        trend_dir = "NEUTRAL"
        if regime_enum in (MarketRegimeType.TRENDING_UP, MarketRegimeType.BREAKOUT) and plus_di.iloc[-1] > minus_di.iloc[-1]:
            trend_dir = "BULLISH"
        elif regime_enum in (MarketRegimeType.TRENDING_DOWN, MarketRegimeType.BREAKOUT) and minus_di.iloc[-1] > plus_di.iloc[-1]:
            trend_dir = "BEARISH"

        return RegimeSnapshot(
            regime=regime_enum,
            confidence=float(last_row["regime_confidence"]),
            adx=float(adx_val.iloc[-1]),
            atr_pct=float(atr_pct),
            bb_width=float(bb_width.iloc[-1]),
            rsi=float(rsi_val.iloc[-1]),
            trend_direction=trend_dir,
        )
