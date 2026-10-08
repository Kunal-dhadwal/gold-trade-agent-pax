"""Deterministic AST interpreter and execution engine for StrategyProposal contracts."""

from datetime import datetime
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

from src.core.constants import OrderDirection, MarketRegimeType
from src.core.logger import logger
from src.quant.indicators.moving_averages import sma, ema
from src.quant.indicators.momentum import rsi, macd
from src.quant.indicators.volatility import atr, bollinger_bands
from src.quant.indicators.trend import adx, donchian_channels
from src.quant.sessions import get_market_session
from src.strategy.base import BaseStrategy, StrategySignal
from src.strategy.parameters import ParameterSpec
from src.strategy.contracts import (
    ConditionComparison,
    ConditionNode,
    IndicatorNameEnum,
    IndicatorSpec,
    StrategyProposal,
    TradeDirection,
    ValueRef,
    ValueRefType,
)


def compute_indicators(
    df: pd.DataFrame,
    indicator_specs: List[IndicatorSpec],
    parameters: Optional[Dict[str, Any]] = None,
) -> Dict[str, pd.Series]:
    """Compute and cache all required indicator series for a strategy proposal."""
    params = parameters or {}
    indicators: Dict[str, pd.Series] = {}

    # Ensure baseline ATR is always available for stop loss / volatility sizing
    if "atr_baseline" not in indicators:
        indicators["atr_baseline"] = atr(df, period=14)

    for spec in indicator_specs:
        alias = spec.alias.lower()
        inputs = spec.inputs.copy()

        # Parameter override if defined
        for k in inputs.keys():
            param_key = f"{alias}_{k}"
            if param_key in params:
                inputs[k] = params[param_key]
            elif k in params:
                inputs[k] = params[k]

        if spec.name == IndicatorNameEnum.EMA:
            period = int(inputs.get("period", 20))
            source_col = str(inputs.get("source", "close")).lower()
            source = df[source_col] if source_col in df.columns else df["close"]
            indicators[alias] = ema(source, period=period)

        elif spec.name == IndicatorNameEnum.SMA:
            period = int(inputs.get("period", 20))
            source_col = str(inputs.get("source", "close")).lower()
            source = df[source_col] if source_col in df.columns else df["close"]
            indicators[alias] = sma(source, period=period)

        elif spec.name == IndicatorNameEnum.RSI:
            period = int(inputs.get("period", 14))
            indicators[alias] = rsi(df["close"], period=period)

        elif spec.name == IndicatorNameEnum.ATR:
            period = int(inputs.get("period", 14))
            indicators[alias] = atr(df, period=period)

        elif spec.name == IndicatorNameEnum.BOLLINGER:
            period = int(inputs.get("period", 20))
            num_std = float(inputs.get("num_std", 2.0))
            upper, middle, lower, bandwidth, percent_b = bollinger_bands(
                df["close"], period=period, num_std=num_std
            )
            indicators[alias] = middle
            indicators[f"{alias}_upper"] = upper
            indicators[f"{alias}_lower"] = lower
            indicators[f"{alias}_middle"] = middle
            indicators[f"{alias}_bandwidth"] = bandwidth
            indicators[f"{alias}_percent_b"] = percent_b

        elif spec.name == IndicatorNameEnum.MACD:
            fast = int(inputs.get("fast", 12))
            slow = int(inputs.get("slow", 26))
            signal_p = int(inputs.get("signal", 9))
            macd_line, sig_line, hist = macd(
                df["close"], fast=fast, slow=slow, signal=signal_p
            )
            indicators[alias] = macd_line
            indicators[f"{alias}_line"] = macd_line
            indicators[f"{alias}_signal"] = sig_line
            indicators[f"{alias}_hist"] = hist

        elif spec.name == IndicatorNameEnum.ADX:
            period = int(inputs.get("period", 14))
            adx_val, plus_di, minus_di = adx(df, period=period)
            indicators[alias] = adx_val
            indicators[f"{alias}_plus_di"] = plus_di
            indicators[f"{alias}_minus_di"] = minus_di

        elif spec.name == IndicatorNameEnum.DONCHIAN:
            period = int(inputs.get("period", 20))
            upper, lower, mid = donchian_channels(df, period=period)
            indicators[alias] = upper
            indicators[f"{alias}_upper"] = upper
            indicators[f"{alias}_lower"] = lower
            indicators[f"{alias}_mid"] = mid

    return indicators


def evaluate_value_ref(
    ref: ValueRef,
    df: pd.DataFrame,
    indicators: Dict[str, pd.Series],
    idx: int,
    additional_lag: int = 0,
) -> Optional[float]:
    """Deterministically resolve a ValueRef at historical bar index."""
    effective_lag = ref.lag + additional_lag
    target_idx = idx - effective_lag

    if target_idx < 0 or target_idx >= len(df):
        return None

    if ref.type == ValueRefType.CONSTANT:
        return float(ref.value) if ref.value is not None else None

    if ref.type == ValueRefType.PRICE:
        col = (ref.name or "").lower()
        if col in df.columns:
            val = df[col].iloc[target_idx]
            return float(val) if not np.isnan(val) else None
        return None

    if ref.type == ValueRefType.INDICATOR:
        key = (ref.name or "").lower()
        if key in indicators:
            val = indicators[key].iloc[target_idx]
            return float(val) if not np.isnan(val) else None
        return None

    return None


def evaluate_comparison(
    comp: ConditionComparison,
    df: pd.DataFrame,
    indicators: Dict[str, pd.Series],
    idx: int,
) -> bool:
    """Evaluate an atomic ConditionComparison at bar index idx."""
    comparator = comp.comparator

    if comparator in (">", "<", ">=", "<=", "=="):
        left_val = evaluate_value_ref(comp.left, df, indicators, idx)
        right_val = evaluate_value_ref(comp.right, df, indicators, idx)

        if left_val is None or right_val is None:
            return False

        if comparator == ">":
            return left_val > right_val
        elif comparator == "<":
            return left_val < right_val
        elif comparator == ">=":
            return left_val >= right_val
        elif comparator == "<=":
            return left_val <= right_val
        elif comparator == "==":
            return abs(left_val - right_val) < 1e-6

    elif comparator == "CROSS_OVER":
        # Cross Over: Current bar Left > Right AND Previous bar Left <= Right
        curr_left = evaluate_value_ref(comp.left, df, indicators, idx, additional_lag=0)
        curr_right = evaluate_value_ref(comp.right, df, indicators, idx, additional_lag=0)
        prev_left = evaluate_value_ref(comp.left, df, indicators, idx, additional_lag=1)
        prev_right = evaluate_value_ref(comp.right, df, indicators, idx, additional_lag=1)

        if any(v is None for v in (curr_left, curr_right, prev_left, prev_right)):
            return False

        return (curr_left > curr_right) and (prev_left <= prev_right)

    elif comparator == "CROSS_UNDER":
        # Cross Under: Current bar Left < Right AND Previous bar Left >= Right
        curr_left = evaluate_value_ref(comp.left, df, indicators, idx, additional_lag=0)
        curr_right = evaluate_value_ref(comp.right, df, indicators, idx, additional_lag=0)
        prev_left = evaluate_value_ref(comp.left, df, indicators, idx, additional_lag=1)
        prev_right = evaluate_value_ref(comp.right, df, indicators, idx, additional_lag=1)

        if any(v is None for v in (curr_left, curr_right, prev_left, prev_right)):
            return False

        return (curr_left < curr_right) and (prev_left >= prev_right)

    return False


def evaluate_condition_node(
    node: ConditionNode,
    df: pd.DataFrame,
    indicators: Dict[str, pd.Series],
    idx: int,
) -> bool:
    """Evaluate a ConditionNode containing conjunctions or disjunctions."""
    results = [
        evaluate_comparison(c, df, indicators, idx)
        for c in node.conditions
    ]

    if node.operator == "AND":
        return all(results)
    elif node.operator == "OR":
        return any(results)

    return False


def normalize_regime_labels(val: Any) -> set:
    """Normalize string or enum regime representation into canonical matchable strings."""
    if val is None:
        return set()
    s = str(val.value if hasattr(val, "value") else val).upper().strip()
    matches = {s}
    if s in ("TRENDING_BULL", "TRENDING_UP"):
        matches.update({"TRENDING_BULL", "TRENDING_UP"})
    elif s in ("TRENDING_BEAR", "TRENDING_DOWN"):
        matches.update({"TRENDING_BEAR", "TRENDING_DOWN"})
    return matches


class ASTStrategy(BaseStrategy):
    """Executable quantitative strategy compiled directly from a StrategyProposal AST."""

    def __init__(
        self,
        proposal: StrategyProposal,
        parameters: Optional[Dict[str, Any]] = None,
    ):
        self.proposal = proposal
        self.strategy_id = str(proposal.strategy_id)
        self.name = proposal.name
        self.category = "AI_GENERATED"
        self.version_tag = "v1"
        self.description = proposal.hypothesis
        super().__init__(parameters=parameters)

    def define_parameters(self) -> List[ParameterSpec]:
        specs: List[ParameterSpec] = []
        for name, p in self.proposal.parameters.items():
            specs.append(
                ParameterSpec(
                    name=name,
                    param_type=p.type,
                    default=p.default,
                    min_value=p.min_value,
                    max_value=p.max_value,
                    step=p.step,
                )
            )
        return specs

    def generate_signals(self, df: pd.DataFrame) -> List[StrategySignal]:
        """Generate deterministic signals by evaluating the AST rules over historical bars."""
        if df.empty or len(df) < 5:
            return []

        indicators = compute_indicators(
            df=df,
            indicator_specs=self.proposal.indicators,
            parameters=self.parameters,
        )

        signals: List[StrategySignal] = []
        allowed_sessions = set(self.proposal.constraints.allowed_sessions)
        is_all_sessions = "ALL" in allowed_sessions

        # Build normalized allowed target regimes for entry eligibility (P2-2 Resolution)
        allowed_target_regimes: set = set()
        for r in self.proposal.target_regimes:
            allowed_target_regimes.update(normalize_regime_labels(r))
        has_regime_col = "regime" in df.columns

        is_long = self.proposal.direction == TradeDirection.LONG
        order_dir = OrderDirection.LONG if is_long else OrderDirection.SHORT

        in_position = False
        bars_held = 0
        max_holding = self.proposal.constraints.max_holding_bars

        for i in range(len(df)):
            row = df.iloc[i]
            ts = row["timestamp"]
            close_p = float(row["close"])

            # Session filter
            if not is_all_sessions:
                sess = get_market_session(ts).value
                if sess not in allowed_sessions:
                    if in_position:
                        bars_held += 1
                        if bars_held >= max_holding:
                            in_position = False
                    continue

            # Position holding counter
            if in_position:
                bars_held += 1
                # Check exit rules
                if evaluate_condition_node(self.proposal.exit_rules, df, indicators, i):
                    in_position = False
                    bars_held = 0
                elif bars_held >= max_holding:
                    in_position = False
                    bars_held = 0
                continue

            # Evaluate entry rules with active target_regimes filter (P2-2 Resolution)
            if has_regime_col:
                bar_regime_raw = row["regime"]
                bar_regime_matches = normalize_regime_labels(bar_regime_raw)
                if not bar_regime_matches.intersection(allowed_target_regimes):
                    continue

            if evaluate_condition_node(self.proposal.entry_rules, df, indicators, i):
                # Calculate deterministic SL and TP price levels
                atr_val = (
                    float(indicators["atr_baseline"].iloc[i])
                    if not np.isnan(indicators["atr_baseline"].iloc[i])
                    else 2.0
                )
                if atr_val <= 0:
                    atr_val = 2.0

                sl_spec = self.proposal.stop_loss
                tp_spec = self.proposal.take_profit

                if sl_spec.type in ("ATR_MULTIPLE", "TRAILING_ATR"):
                    sl_dist = atr_val * sl_spec.multiplier_or_value
                else:  # FIXED_POINTS
                    sl_dist = sl_spec.multiplier_or_value

                if tp_spec.type == "ATR_MULTIPLE":
                    tp_dist = atr_val * tp_spec.multiplier_or_value
                elif tp_spec.type == "FIXED_POINTS":
                    tp_dist = tp_spec.multiplier_or_value
                else:  # RISK_REWARD_RATIO
                    tp_dist = sl_dist * tp_spec.multiplier_or_value

                # Bound minimum distances to avoid zero-division or instant fills
                sl_dist = max(sl_dist, 0.50)
                tp_dist = max(tp_dist, 0.75)

                if is_long:
                    sl_price = round(close_p - sl_dist, 2)
                    tp_price = round(close_p + tp_dist, 2)
                else:
                    sl_price = round(close_p + sl_dist, 2)
                    tp_price = round(close_p - tp_dist, 2)

                rr_ratio = round(tp_dist / sl_dist, 2)

                # Determine signal regime
                if has_regime_col:
                    bar_reg_str = str(
                        row["regime"].value if hasattr(row["regime"], "value") else row["regime"]
                    ).upper().strip()
                    if bar_reg_str in MarketRegimeType._value2member_map_:
                        signal_regime = MarketRegimeType(bar_reg_str)
                    elif bar_reg_str == "TRENDING_BULL":
                        signal_regime = MarketRegimeType.TRENDING_UP
                    elif bar_reg_str == "TRENDING_BEAR":
                        signal_regime = MarketRegimeType.TRENDING_DOWN
                    else:
                        signal_regime = (
                            MarketRegimeType.TRENDING_UP
                            if is_long
                            else MarketRegimeType.TRENDING_DOWN
                        )
                else:
                    signal_regime = (
                        MarketRegimeType.TRENDING_UP
                        if is_long
                        else MarketRegimeType.TRENDING_DOWN
                    )

                signals.append(
                    StrategySignal(
                        timestamp=ts,
                        direction=order_dir,
                        entry_price=close_p,
                        stop_loss=sl_price,
                        take_profit=tp_price,
                        suggested_size=1.0,
                        risk_reward=rr_ratio,
                        regime=signal_regime,
                        reason=f"AST Entry Rule triggered for {self.proposal.name}",
                        metadata={
                            "strategy_id": str(self.proposal.strategy_id),
                            "direction": self.proposal.direction.value,
                            "holding_bars_max": max_holding,
                        },
                    )
                )
                in_position = True
                bars_held = 0

        return signals
