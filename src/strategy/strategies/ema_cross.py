"""Dual EMA Trend-Following strategy with ATR risk brackets."""

from datetime import datetime
from typing import Any, Dict, List
import pandas as pd
from src.core.constants import OrderDirection, MarketRegimeType
from src.quant.indicators.moving_averages import ema
from src.quant.indicators.volatility import atr
from src.strategy.base import BaseStrategy, StrategySignal
from src.strategy.parameters import ParameterSpec


class EMACrossoverStrategy(BaseStrategy):
    strategy_id = "gold_ema_trend_cross"
    name = "Gold Dual EMA Trend Cross"
    category = "TREND_FOLLOWING"
    version_tag = "v1"
    description = "Trend-following strategy entering on fast/slow EMA cross aligned with 200 EMA trend filter."

    def define_parameters(self) -> List[ParameterSpec]:
        return [
            ParameterSpec(name="fast_ema", param_type="int", default=12, min_value=5, max_value=50),
            ParameterSpec(name="slow_ema", param_type="int", default=26, min_value=15, max_value=100),
            ParameterSpec(name="trend_ema", param_type="int", default=200, min_value=20, max_value=300),
            ParameterSpec(name="atr_period", param_type="int", default=14, min_value=7, max_value=30),
            ParameterSpec(name="sl_atr_mult", param_type="float", default=1.5, min_value=0.5, max_value=4.0),
            ParameterSpec(name="rr_ratio", param_type="float", default=2.0, min_value=1.5, max_value=5.0),
        ]

    def generate_signals(self, df: pd.DataFrame) -> List[StrategySignal]:
        min_bars = int(self.get_parameter("trend_ema")) + 5
        if len(df) < min_bars:
            return []

        fast_p = int(self.get_parameter("fast_ema"))
        slow_p = int(self.get_parameter("slow_ema"))
        trend_p = int(self.get_parameter("trend_ema"))
        atr_p = int(self.get_parameter("atr_period"))
        sl_mult = float(self.get_parameter("sl_atr_mult"))
        rr = float(self.get_parameter("rr_ratio"))

        fast_line = ema(df["close"], fast_p)
        slow_line = ema(df["close"], slow_p)
        trend_line = ema(df["close"], trend_p)
        atr_line = atr(df, atr_p)

        signals: List[StrategySignal] = []

        for i in range(min_bars, len(df)):
            prev_fast = fast_line.iloc[i - 1]
            prev_slow = slow_line.iloc[i - 1]
            curr_fast = fast_line.iloc[i]
            curr_slow = slow_line.iloc[i]

            close_p = df["close"].iloc[i]
            trend_val = trend_line.iloc[i]
            atr_val = atr_line.iloc[i]
            ts = df["timestamp"].iloc[i]

            # Bullish Crossover: Fast crosses above Slow AND Close > Trend EMA
            if prev_fast <= prev_slow and curr_fast > curr_slow and close_p > trend_val:
                sl = close_p - (atr_val * sl_mult)
                risk = close_p - sl
                tp = close_p + (risk * rr)
                signals.append(StrategySignal(
                    timestamp=ts,
                    direction=OrderDirection.LONG,
                    entry_price=close_p,
                    stop_loss=sl,
                    take_profit=tp,
                    risk_reward=rr,
                    regime=MarketRegimeType.TRENDING_UP,
                    reason="Bullish EMA crossover above 200 EMA",
                ))

            # Bearish Crossunder: Fast crosses below Slow AND Close < Trend EMA
            elif prev_fast >= prev_slow and curr_fast < curr_slow and close_p < trend_val:
                sl = close_p + (atr_val * sl_mult)
                risk = sl - close_p
                tp = close_p - (risk * rr)
                signals.append(StrategySignal(
                    timestamp=ts,
                    direction=OrderDirection.SHORT,
                    entry_price=close_p,
                    stop_loss=sl,
                    take_profit=tp,
                    risk_reward=rr,
                    regime=MarketRegimeType.TRENDING_DOWN,
                    reason="Bearish EMA crossunder below 200 EMA",
                ))

        return signals
