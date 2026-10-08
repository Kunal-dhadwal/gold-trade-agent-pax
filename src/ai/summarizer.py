"""Deterministic Market Feature Summarizer and Quarantined In-Sample Research Context."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, model_validator

from src.core.constants import MarketRegimeType, MarketSession
from src.quant.indicators.volatility import atr
from src.quant.regime.detector import MarketRegimeDetector
from src.quant.sessions import get_market_session
from src.strategy.contracts import RegimeEnum


class SessionVolatilityProfile(BaseModel):
    """Statistical summary of session volatility and activity."""
    session: str
    bar_count: int
    mean_volume: float
    mean_range_usd: float
    atr_14_mean: float


class RegimeDistributionProfile(BaseModel):
    """Statistical breakdown of market regimes and transitions."""
    regime: str
    percentage: float
    avg_duration_bars: float


class MarketBrief(BaseModel):
    """Condensed statistical profile of In-Sample Gold market data."""
    symbol: str = "XAUUSD"
    timeframe: str = "1h"
    start_date: str
    end_date: str
    total_bars: int
    mean_price: float
    price_min: float
    price_max: float
    daily_volatility_pct: float
    sessions: List[SessionVolatilityProfile]
    regimes: List[RegimeDistributionProfile]
    dominant_regime: str
    generated_at: str


class ISDiagnosticRecord(BaseModel):
    """Diagnostic record of In-Sample backtest performance for prior hypotheses."""
    strategy_name: str
    is_sharpe: float
    is_profit_factor: float
    is_max_drawdown_pct: float
    is_trades_count: int
    passed_is_gates: bool
    failure_reason: Optional[str] = None


class ParameterPlateauRecord(BaseModel):
    """Summary of parameter sensitivity observed during In-Sample optimization."""
    parameter_name: str
    best_value: float
    stable_range_min: float
    stable_range_max: float


class StrategyResearchContext(BaseModel):
    """Quarantined In-Sample context container for Agent 3 (Strategy Researcher).

    ARCHITECTURAL SECURITY BOUNDARY:
    This model strictly excludes Out-of-Sample (OOS) P&L, holdout trade curves,
    Walk-Forward forward metrics, and Monte Carlo ruin statistics.
    """
    session_id: UUID = Field(default_factory=uuid4)
    hypothesis_id: UUID = Field(default_factory=uuid4)
    thesis_statement: str
    target_regime: RegimeEnum
    market_brief: MarketBrief
    is_diagnostic_history: List[ISDiagnosticRecord] = Field(default_factory=list)
    parameter_plateaus: List[ParameterPlateauRecord] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def enforce_oos_quarantine(cls, data: Any) -> Any:
        """Adversarial security barrier: strictly reject any payload carrying OOS metrics."""
        if isinstance(data, dict):
            prohibited_keys = {
                "oos_metrics",
                "oos_equity",
                "oos_trades",
                "oos_sharpe",
                "oos_drawdown",
                "walk_forward_efficiency",
                "wfe",
                "monte_carlo_results",
                "probability_of_ruin",
            }
            found = prohibited_keys.intersection(data.keys())
            if found:
                raise ValueError(
                    f"CRITICAL SECURITY VIOLATION: OOS Quarantine breached! "
                    f"Prohibited Out-of-Sample keys detected in research context: {found}"
                )
        return data


class MarketFeatureSummarizer:
    """Deterministic feature aggregator for condensing raw Gold bars into a MarketBrief."""

    def __init__(self):
        self.regime_detector = MarketRegimeDetector()

    def summarize(self, df: pd.DataFrame, symbol: str = "XAUUSD", timeframe: str = "1h") -> MarketBrief:
        """Summarize In-Sample DataFrame into a concise statistical MarketBrief."""
        if df.empty or len(df) < 10:
            raise ValueError("Insufficient data to build MarketBrief (minimum 10 bars required).")

        df_copy = df.copy()
        if "atr" not in df_copy.columns:
            df_copy["atr"] = atr(df_copy, period=14).fillna(2.0)

        df_copy["range"] = df_copy["high"] - df_copy["low"]
        df_copy["session"] = df_copy["timestamp"].map(lambda ts: get_market_session(ts).value)

        # 1. Session statistics
        sessions_summary: List[SessionVolatilityProfile] = []
        for sess_name, group in df_copy.groupby("session"):
            sessions_summary.append(
                SessionVolatilityProfile(
                    session=str(sess_name),
                    bar_count=len(group),
                    mean_volume=round(float(group["volume"].mean()), 2),
                    mean_range_usd=round(float(group["range"].mean()), 2),
                    atr_14_mean=round(float(group["atr"].mean()), 2),
                )
            )

        # 2. Deterministic regime detection
        classified_df = self.regime_detector.classify_series(df_copy)
        regime_counts = classified_df["regime"].value_counts(normalize=True)

        regimes_summary: List[RegimeDistributionProfile] = []
        for reg_name, pct in regime_counts.items():
            regimes_summary.append(
                RegimeDistributionProfile(
                    regime=str(reg_name),
                    percentage=round(float(pct * 100.0), 2),
                    avg_duration_bars=round(float(len(df_copy) * pct / max(len(regimes_summary) + 1, 1)), 1),
                )
            )

        dominant = regime_counts.index[0] if not regime_counts.empty else "RANGING"

        # Volatility percentage
        returns = df_copy["close"].pct_change().dropna()
        daily_vol_pct = round(float(returns.std() * np.sqrt(24) * 100.0), 2)

        return MarketBrief(
            symbol=symbol,
            timeframe=timeframe,
            start_date=df_copy["timestamp"].min().isoformat(),
            end_date=df_copy["timestamp"].max().isoformat(),
            total_bars=len(df_copy),
            mean_price=round(float(df_copy["close"].mean()), 2),
            price_min=round(float(df_copy["low"].min()), 2),
            price_max=round(float(df_copy["high"].max()), 2),
            daily_volatility_pct=daily_vol_pct,
            sessions=sessions_summary,
            regimes=regimes_summary,
            dominant_regime=dominant,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
