# ADR-007: Machine-Readable AI Strategy Contract Specification
**Status:** Approved & Frozen  
**Date:** 2026-10-08  
**Author:** Lead Quantitative Systems Engineer & AI Architect  
**Governing Documents:** `PROJECT_CONSTITUTION.md`, `ARCHITECTURE.md`, `ADR-004-ai-vs-deterministic-boundaries.md`

---

## 1. CONTEXT & PROBLEM STATEMENT

Early iterations of AI trading research commonly suffer from **Ambiguous Generation Failure**:
- LLMs emit free-form natural language trading rules (e.g., *"Buy when momentum is strong and RSI is not oversold, exit on exhaustion"*).
- Free-form text requires unpredictable natural-language parsing or risky dynamic Python `exec()` / `eval()` execution to translate into backtest logic.
- Natural language introduces lookahead bias, semantic ambiguities, unverifiable parameter bounds, and security vulnerabilities (e.g., prompt injection leading to arbitrary code execution).
- Without explicit trade direction (`LONG` vs `SHORT`), deterministic backtesters must guess trading intent.
- Without a typed historical lag/lookback specification, strategies cannot reference prior bar states (e.g., swing highs, previous closes) or are vulnerable to future-bar lookahead leakage.

The system requires an uncompromising, strictly deterministic machine-readable contract that bridges AI strategy ideation and the deterministic backtesting engine without natural-language interpretation.

---

## 2. DECISION

We formally establish the **Machine-Readable AI Strategy Contract**:

### A. The Schema Specification
All strategy proposals emitted by the AI Strategy Researcher must strictly validate against the `StrategyProposalSchema` (Pydantic v2):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "StrategyProposal",
  "type": "object",
  "required": [
    "strategy_id",
    "name",
    "hypothesis",
    "direction",
    "instrument",
    "timeframe",
    "data_source",
    "target_regimes",
    "indicators",
    "parameters",
    "entry_rules",
    "exit_rules",
    "stop_loss",
    "take_profit",
    "position_sizing_reference",
    "constraints",
    "rationale",
    "generated_by"
  ],
  "properties": {
    "strategy_id": { "type": "string", "format": "uuid" },
    "name": { "type": "string", "pattern": "^[A-Z0-9_]{3,32}$" },
    "hypothesis": { "type": "string", "minLength": 20, "maxLength": 500 },
    "direction": { "type": "string", "enum": ["LONG", "SHORT"] },
    "instrument": { "type": "string", "enum": ["XAUUSD", "GC=F", "PAXGUSDT"] },
    "timeframe": { "type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"] },
    "data_source": { "type": "string", "enum": ["DUKASCOPY", "YAHOO", "DELTA_EXCHANGE"] },
    "target_regimes": {
      "type": "array",
      "items": { "type": "string", "enum": ["TRENDING_BULL", "TRENDING_BEAR", "RANGING", "HIGH_VOLATILITY", "LOW_VOLATILITY", "BREAKOUT"] },
      "minItems": 1
    },
    "indicators": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["alias", "name", "inputs"],
        "properties": {
          "alias": { "type": "string", "pattern": "^[a-z0-9_]{2,16}$" },
          "name": { "type": "string", "enum": ["EMA", "SMA", "RSI", "MACD", "ATR", "BOLLINGER", "ADX", "DONCHIAN"] },
          "inputs": { "type": "object" }
        }
      },
      "minItems": 1,
      "maxItems": 6
    },
    "parameters": {
      "type": "object",
      "additionalProperties": {
        "type": "object",
        "required": ["type", "default", "min_value", "max_value", "step"],
        "properties": {
          "type": { "type": "string", "enum": ["INT", "FLOAT"] },
          "default": { "type": "number" },
          "min_value": { "type": "number" },
          "max_value": { "type": "number" },
          "step": { "type": "number" }
        }
      }
    },
    "entry_rules": { "$ref": "#/$defs/ConditionNode" },
    "exit_rules": { "$ref": "#/$defs/ConditionNode" },
    "stop_loss": {
      "type": "object",
      "required": ["type", "multiplier_or_value"],
      "properties": {
        "type": { "type": "string", "enum": ["ATR_MULTIPLE", "FIXED_POINTS", "TRAILING_ATR"] },
        "multiplier_or_value": { "type": "number", "minimum": 0.5, "maximum": 10.0 }
      }
    },
    "take_profit": {
      "type": "object",
      "required": ["type", "multiplier_or_value"],
      "properties": {
        "type": { "type": "string", "enum": ["ATR_MULTIPLE", "FIXED_POINTS", "RISK_REWARD_RATIO"] },
        "multiplier_or_value": { "type": "number", "minimum": 1.0, "maximum": 20.0 }
      }
    },
    "position_sizing_reference": {
      "type": "object",
      "required": ["type", "risk_pct"],
      "properties": {
        "type": { "type": "string", "enum": ["PERCENT_EQUITY"] },
        "risk_pct": { "type": "number", "minimum": 0.1, "maximum": 1.0 }
      }
    },
    "constraints": {
      "type": "object",
      "required": ["max_holding_bars", "allowed_sessions"],
      "properties": {
        "max_holding_bars": { "type": "integer", "minimum": 1, "maximum": 500 },
        "allowed_sessions": {
          "type": "array",
          "items": { "type": "string", "enum": ["LONDON", "NEW_YORK", "ASIAN", "ALL"] }
        }
      }
    },
    "rationale": { "type": "string", "minLength": 20 },
    "generated_by": { "type": "string", "default": "Agent-3-StrategyResearcher" }
  },
  "$defs": {
    "ValueRef": {
      "type": "object",
      "required": ["type"],
      "properties": {
        "type": { "type": "string", "enum": ["PRICE", "INDICATOR", "CONSTANT"] },
        "name": { "type": "string", "pattern": "^[a-z0-9_]+$" },
        "value": { "type": "number" },
        "lag": { "type": "integer", "minimum": 0, "maximum": 20, "default": 0 }
      }
    },
    "ConditionComparison": {
      "type": "object",
      "required": ["left", "comparator", "right"],
      "properties": {
        "left": { "$ref": "#/$defs/ValueRef" },
        "comparator": { "type": "string", "enum": [">", "<", ">=", "<=", "==", "CROSS_OVER", "CROSS_UNDER"] },
        "right": { "$ref": "#/$defs/ValueRef" }
      }
    },
    "ConditionNode": {
      "type": "object",
      "required": ["operator", "conditions"],
      "properties": {
        "operator": { "type": "string", "enum": ["AND", "OR"] },
        "conditions": {
          "type": "array",
          "items": { "$ref": "#/$defs/ConditionComparison" },
          "minItems": 1,
          "maxItems": 5
        }
      }
    }
  }
}
```

---

### B. Explicit Trade Direction Semantics (P1-1 Resolution)

Every `StrategyProposal` must declare an explicit `direction: Literal["LONG", "SHORT"]`. The deterministic backtest engine interprets direction according to the following mathematical specifications:

#### 1. Entry Logic Interpretation
- **LONG Strategies:** When `entry_rules` evaluates to `True` at bar $T$ Close, the engine generates an order to open a Long position at bar $T+1$ Open.
- **SHORT Strategies:** When `entry_rules` evaluates to `True` at bar $T$ Close, the engine generates an order to open a Short position at bar $T+1$ Open.

#### 2. Exit Rules Interpretation
- **LONG Strategies:** When `exit_rules` evaluates to `True`, an active Long position is closed via market sell before SL/TP is hit.
- **SHORT Strategies:** When `exit_rules` evaluates to `True`, an active Short position is closed via market buy to cover before SL/TP is hit.

#### 3. Stop-Loss & Take-Profit Geometry
- **LONG Strategies:**
  - $\text{Stop Loss Price} = P_{\text{entry}} - \text{SL Distance}$ (placed strictly **below** entry price).
  - $\text{Take Profit Price} = P_{\text{entry}} + \text{TP Distance}$ (placed strictly **above** entry price).
- **SHORT Strategies:**
  - $\text{Stop Loss Price} = P_{\text{entry}} + \text{SL Distance}$ (placed strictly **above** entry price).
  - $\text{Take Profit Price} = P_{\text{entry}} - \text{TP Distance}$ (placed strictly **below** entry price).

#### 4. Risk-to-Reward Ratio ($R:R$) Formulation
- **LONG Strategies:**
  $$R:R = \frac{P_{\text{TP}} - P_{\text{entry}}}{P_{\text{entry}} - P_{\text{SL}}} \ge 1.5$$
- **SHORT Strategies:**
  $$R:R = \frac{P_{\text{entry}} - P_{\text{TP}}}{P_{\text{SL}} - P_{\text{entry}}} \ge 1.5$$
- Any strategy where planned $R:R < 1.5$ is rejected at schema validation.

#### 5. Slippage & Half-Spread Friction Application
- **LONG Execution:**
  - Fill Entry: $P_{\text{fill, entry}} = P_{\text{open, } T+1} + \frac{\text{Spread}}{2} + \text{Slippage}$
  - Fill Exit: $P_{\text{fill, exit}} = P_{\text{close, exit}} - \frac{\text{Spread}}{2} - \text{Slippage}$
- **SHORT Execution:**
  - Fill Entry: $P_{\text{fill, entry}} = P_{\text{open, } T+1} - \frac{\text{Spread}}{2} - \text{Slippage}$
  - Fill Exit: $P_{\text{fill, exit}} = P_{\text{close, exit}} + \frac{\text{Spread}}{2} + \text{Slippage}$

#### 6. Maximum Holding Period
- Applies identically to both directions: If $\text{holding\_bars} \ge \text{max\_holding\_bars}$ and neither SL nor TP has been triggered, the position is liquidated on the next bar open with `ExitReason.EXPIRY`.

#### 7. Interface to Phase 1 Backtest Engine
- In Phase 1, `BacktestEngine` accepts signals. When translating `StrategyProposal` into signals:
  - If `direction == "LONG"`, entry emits `OrderSide.BUY`, exit emits `OrderSide.SELL`.
  - If `direction == "SHORT"`, entry emits `OrderSide.SELL`, exit emits `OrderSide.BUY`.

#### 8. Bi-Directional System Policy
- In Phase 2, a `StrategyProposal` is strictly uni-directional. 
- Bi-directional systems are composed by generating two paired, complementary strategy proposals (e.g. `GOLD_MOMENTUM_LONG` and `GOLD_MOMENTUM_SHORT`), each independently validated and scored.

---

### C. AST Lag / Historical Lookback Semantics (P1-2 Resolution)

To support realistic technical analysis without lookahead bias, all dynamic references are formalized using the typed `ValueRef` structure:

```json
{
  "type": "PRICE" | "INDICATOR" | "CONSTANT",
  "name": "close" | "rsi_14",
  "value": 70.0,
  "lag": 0
}
```

#### 1. Lag Indexing Rules
- **`lag = 0`:** The current evaluated bar ($T$). For price, it represents the closing price or bar value at evaluation time.
- **`lag = 1`:** The previous completed bar ($T-1$).
- **`lag = 2`:** Two completed bars prior ($T-2$).
- **Maximum Permitted Lag:** `MAX_LAG = 20`. Lags $> 20$ are rejected by the schema constraint (`maximum: 20`).
- **Negative Lags Strictly Forbidden:** `lag < 0` is physically blocked by the schema constraint (`minimum: 0`). Lookahead references (e.g. `lag = -1`, `close[+1]`) are impossible.

#### 2. Indicator Calculation Sequence
1. Indicators are computed across the full contiguous series first using Phase 1 deterministic indicator functions.
2. The indicator series is then shifted by `lag`: $\text{Series}_{\text{eval}} = \text{Series}.\text{shift}(\text{lag})$.
3. Comparisons are evaluated bar-by-bar using vectorized boolean masks.

#### 3. Missing Historical Bars Handling
- If the bar index $T < \text{warmup\_period} + \text{lag}$, the comparison evaluates strictly to `False`. The engine never raises exceptions or backfills with future data.

#### 4. Interaction with `CROSS_OVER` and `CROSS_UNDER`
- The `CROSS_OVER(left, right)` operator at Bar $T$ with `lag = 0` evaluates:
  $$\text{left}[T] > \text{right}[T] \quad \text{AND} \quad \text{left}[T-1] \le \text{right}[T-1]$$
- If `lag = 1` is specified, it evaluates whether the crossover occurred at Bar $T-1$:
  $$\text{left}[T-1] > \text{right}[T-1] \quad \text{AND} \quad \text{left}[T-2] \le \text{right}[T-2]$$

---

### D. Concrete AST Representation Examples

#### 5 Valid AST Examples:

##### Example 1: Valid LONG EMA Crossover with Previous Bar Confirmation
```json
{
  "operator": "AND",
  "conditions": [
    {
      "left": { "type": "INDICATOR", "name": "ema_fast", "lag": 0 },
      "comparator": "CROSS_OVER",
      "right": { "type": "INDICATOR", "name": "ema_slow", "lag": 0 }
    },
    {
      "left": { "type": "PRICE", "name": "close", "lag": 1 },
      "comparator": ">",
      "right": { "type": "INDICATOR", "name": "ema_slow", "lag": 1 }
    }
  ]
}
```

##### Example 2: Valid SHORT RSI Overbought Reversal
```json
{
  "operator": "AND",
  "conditions": [
    {
      "left": { "type": "INDICATOR", "name": "rsi_14", "lag": 1 },
      "comparator": ">=",
      "right": { "type": "CONSTANT", "value": 70.0, "lag": 0 }
    },
    {
      "left": { "type": "INDICATOR", "name": "rsi_14", "lag": 0 },
      "comparator": "<",
      "right": { "type": "CONSTANT", "value": 70.0, "lag": 0 }
    }
  ]
}
```

##### Example 3: Valid LONG Breakout Above Prior Bar High
```json
{
  "operator": "AND",
  "conditions": [
    {
      "left": { "type": "PRICE", "name": "close", "lag": 0 },
      "comparator": ">",
      "right": { "type": "PRICE", "name": "high", "lag": 1 }
    },
    {
      "left": { "type": "INDICATOR", "name": "adx_14", "lag": 0 },
      "comparator": ">",
      "right": { "type": "CONSTANT", "value": 25.0, "lag": 0 }
    }
  ]
}
```

##### Example 4: Valid SHORT Donchian Breakdown
```json
{
  "operator": "AND",
  "conditions": [
    {
      "left": { "type": "PRICE", "name": "close", "lag": 0 },
      "comparator": "<",
      "right": { "type": "INDICATOR", "name": "donchian_lower", "lag": 1 }
    }
  ]
}
```

##### Example 5: Valid Exit Rule on Trailing Volatility Threshold
```json
{
  "operator": "OR",
  "conditions": [
    {
      "left": { "type": "INDICATOR", "name": "atr_14", "lag": 0 },
      "comparator": ">",
      "right": { "type": "CONSTANT", "value": 15.0, "lag": 0 }
    },
    {
      "left": { "type": "PRICE", "name": "close", "lag": 0 },
      "comparator": "<",
      "right": { "type": "INDICATOR", "name": "ema_fast", "lag": 0 }
    }
  ]
}
```

---

#### 5 Invalid AST Examples (Strictly Rejected at Schema Validation):

##### Invalid Example 1: Negative Lag (Lookahead Bias Attempt)
```json
{
  "left": { "type": "PRICE", "name": "close", "lag": -1 },
  "comparator": ">",
  "right": { "type": "PRICE", "name": "open", "lag": 0 }
}
```
*Rejection Reason:* Schema constraint violation: `lag` has `minimum: 0`. Negative values are blocked.

##### Invalid Example 2: Excessive Lag Exceeding Upper Bound
```json
{
  "left": { "type": "INDICATOR", "name": "rsi_14", "lag": 50 },
  "comparator": "<",
  "right": { "type": "CONSTANT", "value": 30.0, "lag": 0 }
}
```
*Rejection Reason:* Schema constraint violation: `lag` has `maximum: 20`. Lag 50 is rejected.

##### Invalid Example 3: Constant with Non-Zero Lag
```json
{
  "left": { "type": "INDICATOR", "name": "rsi_14", "lag": 0 },
  "comparator": "<",
  "right": { "type": "CONSTANT", "value": 30.0, "lag": 2 }
}
```
*Rejection Reason:* Semantic validator violation: `type == "CONSTANT"` must have `lag == 0`. Constants do not shift across time.

##### Invalid Example 4: Indicator Not Defined in Contract Allowlist
```json
{
  "left": { "type": "INDICATOR", "name": "ichimoku_tenkan", "lag": 0 },
  "comparator": ">",
  "right": { "type": "PRICE", "name": "close", "lag": 0 }
}
```
*Rejection Reason:* Unapproved indicator: `ichimoku_tenkan` is not in the deterministic indicator allowlist (`EMA`, `SMA`, `RSI`, `MACD`, `ATR`, `BOLLINGER`, `ADX`, `DONCHIAN`).

##### Invalid Example 5: Arbitrary Python Expression String
```json
{
  "left": { "type": "PRICE", "name": "__import__('os').system('ls')", "lag": 0 },
  "comparator": ">",
  "right": { "type": "CONSTANT", "value": 100.0, "lag": 0 }
}
```
*Rejection Reason:* Schema regex violation: `name` pattern `^[a-z0-9_]+$` rejects all parentheses, quotes, and punctuation, completely eliminating code injection.

---

## 3. CONSEQUENCES

### Positive:
- **100% Directional Clarity:** Eliminates ambiguous backtester interpretations; Long and Short trading mechanics are mathematically distinct.
- **Realistic Historical Technical Analysis:** Allows strategies to reference prior bar values and swing points safely up to 20 bars back.
- **Zero Lookahead / Future Leakage:** Negative lags are physically unrepresentable in the typed schema.
- **Zero Attack Surface:** Strict regex and Pydantic types eliminate code injection attacks.

### Negative / Trade-offs:
- Slightly more verbose JSON payloads due to structured `ValueRef` objects.
- Strategies cannot reference more than 20 bars back without calculating dedicated multi-period indicators.

---

## 4. COMPLIANCE & VERIFICATION
1. Unit tests in `tests/unit/strategy/test_contracts.py` must verify that schemas without `direction` or with `lag < 0` or `lag > 20` fail validation with explicit `ValidationError` exceptions.
2. Backtest execution tests must verify that `direction == "LONG"` enters on ask and exits on bid, while `direction == "SHORT"` enters on bid and exits on ask.
