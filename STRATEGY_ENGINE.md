# STRATEGY ENGINE SPECIFICATION
*Strategy Base Interfaces, Parameter Management, Versioning, and Multi-Regime Coupling*

---

## 1. STRATEGY INTERFACE & LIFE CYCLE

All strategies inherit from `BaseStrategy` (`src/strategy/base.py`):

```python
class BaseStrategy(ABC):
    def __init__(self, parameters: Dict[str, Any]):
        self.parameters = parameters

    @abstractmethod
    def generate_signals(self, market_data: pd.DataFrame) -> List[Signal]:
        """Compute signals based strictly on historical bars up to current time."""
        pass

    @abstractmethod
    def required_indicators(self) -> List[str]:
        """List indicator names required for calculation."""
        pass
```

---

## 2. STRATEGY VERSIONING & STATUS WORKFLOW

Every strategy version tracks a strict state machine:

```
[RESEARCH]
    | (Deterministic In-Sample Backtest Passes)
    v
[BACKTESTED]
    | (Walk-Forward, Monte Carlo, Stress Test Pass)
    v
[VALIDATION]
    | (Holdout Out-of-Sample Verification Passes)
    v
[OOS_PASSED]
    | (Activated on Real-Time Paper Feed)
    v
[PAPER_TRADING]
    | (Minimum 30-day paper test + slippage reality check)
    v
[LIVE_CANDIDATE]
    | (Explicit User Sign-Off + LIVE_TRADING_ENABLED=true)
    v
[ACTIVE]

At any step:
  If failure criteria met -> [REJECTED]
  If live degradation observed -> [RETIRED]
```

---

## 3. REFERENCE STRATEGIES (PHASE 1 FOUNDATION)

To establish mathematical and backtesting baselines in Phase 1, three deterministic reference strategies are implemented:

1. **Dual EMA Trend Filter (`ema_trend_cross`):**
   - Fast EMA (e.g., 20) and Slow EMA (e.g., 50) crossover.
   - Long when Fast crosses above Slow and price $> \text{EMA}_{200}$.
   - Stop Loss: $1.5 \times \text{ATR}_{14}$.
   - Take Profit: $3.0 \times \text{ATR}_{14}$ ($1:2$ Risk/Reward).
2. **ATR Volatility Breakout (`atr_breakout`):**
   - Channel breakout based on $N$-period high/low bounded by ATR expansion.
   - Dynamic trailing stop loss based on Chandelier exit ($3 \times \text{ATR}$).
3. **RSI Mean Reversion with Bollinger Bands (`rsi_mean_reversion`):**
   - Active only during `RANGING` or `LOW_VOLATILITY` regimes.
   - Entry when RSI $< 30$ and price touches lower Bollinger Band.
   - Exit at midline (20 SMA) or opposite band.
