"""RSI Mean Reversion with Bollinger Bands Strategy."""

from typing import List
import pandas as pd
from src.core.constants import OrderDirection, MarketRegimeType
from src.quant.indicators.momentum import rsi
from src.quant.indicators.volatility import atr, bollinger_bands
from src.strategy.base import BaseStrategy, StrategySignal
from src.strategy.parameters import ParameterSpec


class RSIMeanReversionStrategy(BaseStrategy):
    strategy_id = "gold_rsi_mean_reversion"
    name = "Gold RSI Mean Reversion"
    category = "MEAN_REVERSION"
    version_tag = "v1"
    description = "Mean reversion buying oversold RSI touches on lower Bollinger Band and selling overbought."

    def define_parameters(self) -> List[ParameterSpec]:
        return [
            ParameterSpec(name="rsi_period", param_type="int", default=14, min_value=7, max_value=25),
            ParameterSpec(name="rsi_oversold", param_type="float", default=30.0, min_value=15.0, max_value=40.0),
            ParameterSpec(name="rsi_overbought", param_type="float", default=70.0, min_value=60.0, max_value=85.0),
            ParameterSpec(name="bb_period", param_type="int", default=20, min_value=10, max_value=40),
            ParameterSpec(name="bb_std", param_type="float", default=2.0, min_value=1.5, max_value=3.0),
            ParameterSpec(name="sl_atr_mult", param_type="float", default=1.5, min_value=0.8, max_value=3.0),
        ]

    def generate_signals(self, df: pd.DataFrame) -> List[StrategySignal]:
        rsi_p = int(self.get_parameter("rsi_period"))
        oversold = float(self.get_parameter("rsi_oversold"))
        overbought = float(self.get_parameter("rsi_overbought"))
        bb_p = int(self.get_parameter("bb_period"))
        bb_std = float(self.get_parameter("bb_std"))
        sl_mult = float(self.get_parameter("sl_atr_mult"))

        min_bars = max(rsi_p, bb_p) + 5
        if len(df) < min_bars:
            return []

        rsi_vals = rsi(df["close"], rsi_p)
        upper, middle, lower, _, _ = bollinger_bands(df["close"], bb_p, bb_std)
        atr_vals = atr(df, 14)

        signals: List[StrategySignal] = []

        for i in range(min_bars, len(df)):
            close_p = df["close"].iloc[i]
            low_p = df["low"].iloc[i]
            high_p = df["high"].iloc[i]
            rsi_v = rsi_vals.iloc[i]
            lower_b = lower.iloc[i]
            upper_b = upper.iloc[i]
            mid_b = middle.iloc[i]
            atr_v = atr_vals.iloc[i]
            ts = df["timestamp"].iloc[i]

            # Long Reversion: RSI < 30 and Low <= Lower Band
            if rsi_v <= oversold and low_p <= lower_b:
                sl = close_p - (atr_v * sl_mult)
                tp = mid_b if mid_b > close_p else close_p + (close_p - sl) * 1.5
                risk = max(close_p - sl, 0.5)
                rr = (tp - close_p) / risk
                signals.append(StrategySignal(
                    timestamp=ts,
                    direction=OrderDirection.LONG,
                    entry_price=close_p,
                    stop_loss=sl,
                    take_profit=tp,
                    risk_reward=max(rr, 1.5),
                    regime=MarketRegimeType.RANGING,
                    reason=f"Oversold RSI ({rsi_v:.1f}) touch on lower Bollinger Band",
                ))

            # Short Reversion: RSI > 70 and High >= Upper Band
            elif rsi_v >= overbought and high_p >= upper_b:
                sl = close_p + (atr_v * sl_mult)
                tp = mid_b if mid_b < close_p else close_p - (sl - close_p) * 1.5
                risk = max(sl - close_p, 0.5)
                rr = (close_p - tp) / risk
                signals.append(StrategySignal(
                    timestamp=ts,
                    direction=OrderDirection.SHORT,
                    entry_price=close_p,
                    stop_loss=sl,
                    take_profit=tp,
                    risk_reward=max(rr, 1.5),
                    regime=MarketRegimeType.RANGING,
                    reason=f"Overbought RSI ({rsi_v:.1f}) touch on upper Bollinger Band",
                ))

        return signals
