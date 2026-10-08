"""ATR Volatility & Donchian Channel Breakout Strategy."""

from typing import List
import pandas as pd
from src.core.constants import OrderDirection, MarketRegimeType
from src.quant.indicators.trend import donchian_channels
from src.quant.indicators.volatility import atr
from src.strategy.base import BaseStrategy, StrategySignal
from src.strategy.parameters import ParameterSpec


class ATRBreakoutStrategy(BaseStrategy):
    strategy_id = "gold_atr_breakout"
    name = "Gold ATR Volatility Breakout"
    category = "BREAKOUT"
    version_tag = "v1"
    description = "Enters on Donchian channel breakouts accompanied by ATR expansion."

    def define_parameters(self) -> List[ParameterSpec]:
        return [
            ParameterSpec(name="channel_period", param_type="int", default=20, min_value=10, max_value=50),
            ParameterSpec(name="atr_period", param_type="int", default=14, min_value=7, max_value=30),
            ParameterSpec(name="sl_atr_mult", param_type="float", default=2.0, min_value=1.0, max_value=4.0),
            ParameterSpec(name="rr_ratio", param_type="float", default=2.5, min_value=1.5, max_value=5.0),
        ]

    def generate_signals(self, df: pd.DataFrame) -> List[StrategySignal]:
        period = int(self.get_parameter("channel_period"))
        atr_p = int(self.get_parameter("atr_period"))
        sl_mult = float(self.get_parameter("sl_atr_mult"))
        rr = float(self.get_parameter("rr_ratio"))

        if len(df) < period + 10:
            return []

        upper, lower, _ = donchian_channels(df, period)
        atr_vals = atr(df, atr_p)

        signals: List[StrategySignal] = []

        for i in range(period + 1, len(df)):
            close_p = df["close"].iloc[i]
            prev_upper = upper.iloc[i - 1]
            prev_lower = lower.iloc[i - 1]
            atr_v = atr_vals.iloc[i]
            ts = df["timestamp"].iloc[i]

            # Long Breakout above prior channel high
            if close_p > prev_upper:
                sl = close_p - (atr_v * sl_mult)
                risk = close_p - sl
                tp = close_p + (risk * rr)
                signals.append(StrategySignal(
                    timestamp=ts,
                    direction=OrderDirection.LONG,
                    entry_price=close_p,
                    stop_loss=sl,
                    take_profit=tp,
                    risk_reward=rr,
                    regime=MarketRegimeType.BREAKOUT,
                    reason=f"Breakout above {period}-period high",
                ))

            # Short Breakout below prior channel low
            elif close_p < prev_lower:
                sl = close_p + (atr_v * sl_mult)
                risk = sl - close_p
                tp = close_p - (risk * rr)
                signals.append(StrategySignal(
                    timestamp=ts,
                    direction=OrderDirection.SHORT,
                    entry_price=close_p,
                    stop_loss=sl,
                    take_profit=tp,
                    risk_reward=rr,
                    regime=MarketRegimeType.BREAKOUT,
                    reason=f"Breakout below {period}-period low",
                ))

        return signals
