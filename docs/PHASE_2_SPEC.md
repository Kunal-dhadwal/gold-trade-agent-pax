# PHASE 2 TECHNICAL SPECIFICATION & ARCHITECTURAL CONTRACT
**System:** Gold AI Quantitative Trading System (`AGentTrXPAUS`)  
**Phase:** Phase 2 — AI Multi-Agent Strategy Generation & Validation Engine  
**Status:** Canonical Design Specification & Architectural Baseline (Post-Audit Remediated)  
**Governing Documents:**  
1. `PROJECT_CONSTITUTION.md`  
2. `ARCHITECTURE.md`  
3. `docs/PHASE_1_SPEC.md`  
4. `docs/ADR/` (ADR-001 through ADR-011)  
**Baseline Release Tag:** `v1.1.0-phase1-verified`

---

## 1. PURPOSE, SCOPE & SYSTEM BOUNDARIES

Phase 2 builds directly upon the verified Phase 1 quantitative foundation (`v1.1.0-phase1-verified`) by introducing an autonomous, multi-agent AI research laboratory. The engine uses Large Language Models (LLMs) to formulate falsifiable economic hypotheses, propose formal trading strategies, and systematically challenge them through exhaustive deterministic validation.

### What Phase 2 Explicitly Includes:
1. **Multi-Agent Research Ecosystem:** Five specialized AI agents operating under strict division of labor:
   - Market Analyst
   - Regime Analyst
   - Strategy Researcher
   - Quant Validator
   - Strategy Evaluation / Performance Analyst
2. **Provider Abstraction Layer:** Local-first, zero-cost LLM interface supporting local Ollama (`deepseek-r1:8b`, `qwen2.5-coder:7b`, `llama3.1:8b`) as primary and Google Gemini API (free tier) as secondary.
3. **Machine-Readable Strategy Contract:** Strict Pydantic/JSON Schema defining strategy logic as an Abstract Syntax Tree (AST) of Boolean conditions without arbitrary Python code execution, featuring explicit trade direction (`LONG` / `SHORT`) and typed historical lookback lags (`ValueRef`).
4. **Context-Optimized Feature Summarizer:** Deterministic data condenser converting hundreds of thousands of raw Gold bars into concise statistical market profiles, preventing token exhaustion and high latency.
5. **Deterministic Validation Gatekeeper:** Configurable policy-based Tier-1 hard rejection gates eliminating strategies failing sample size, profit factor, drawdown, or expectancy standards.
6. **Walk-Forward Analysis (WFA) Engine:** Rolling out-of-sample forward verification testing Walk-Forward Efficiency (WFE) across Gold market cycles, with mathematically robust non-positive denominator handling.
7. **Monte Carlo Robustness Engine:** Bootstrap trade order reshuffling (2,000 paths), liquidity friction perturbations ($+50\%$ slippage/spread), and parameter plateau stability checks.
8. **Composite Strategy Scoring (CSS):** Multi-factor deterministic ranking metric aggregating OOS return, drawdown risk, WFE, Monte Carlo ruin resistance, and parameter smoothness.
9. **Two-Tier Research Memory with OOS Quarantine:** Relational PostgreSQL storage partitioned into `IS_RESEARCH_MEMORY` (accessible to ideation) and `OOS_VALIDATION_MEMORY` (strictly air-gapped from strategy generation).
10. **Phase 2 REST API Suite with Async Job Abstraction:** FastAPI endpoints for research sessions, hypotheses, strategy proposals, and asynchronous background execution for heavy validation jobs.
11. **Anti-Overfitting Controls:** Hypothesis pre-registration, search budget quotas (max 10 iterations per hypothesis), AST deduplication, Deflated Sharpe Ratio calculation, and a cascading early-exit pipeline.
12. **Cryptographic AI Audit Trail:** Immutable SHA-256 prompt hashing, token accounting, and reasoning logs stored in `agent_decisions`.

### What Phase 2 Strictly Excludes (Frozen Boundaries):
- **Zero Live Execution Authority:** No access to live order routing, exchange authentication, or trading accounts (reserved for Phase 4).
- **Zero Paper Trading Runtime:** No real-time trade monitoring daemons, paper order matching engines, or real-time WebSocket listeners (reserved for Phase 3).
- **Zero React Frontend:** No web UI or client application is implemented (reserved for Phase 3).
- **Zero Redis Dependency:** Phase 2 remains 100% operational with in-memory local caching and relational persistence without requiring a Redis daemon.
- **Zero Pine Script Generation:** Code generation for TradingView Pine Script is deferred until strategy candidates pass Phase 2 acceptance.
- **Zero Arbitrary Code Execution:** The system forbids executing dynamic Python scripts (`eval`, `exec`) emitted by LLMs.

---

## 2. THE NON-NEGOTIABLE ARCHITECTURAL PRINCIPLE

```text
+---------------------------------------------------------------------------------------------------+
| THE GOVERNING ARCHITECTURAL LAW                                                                   |
|                                                                                                   |
|                      1. AI PROPOSES.                                                              |
|                      2. DETERMINISTIC QUANTITATIVE SYSTEMS VERIFY.                                |
|                      3. RISK ENGINE HAS UNCONDITIONAL VETO AUTHORITY.                             |
+---------------------------------------------------------------------------------------------------+
```

### Invariant Rules:
1. **Untrusted Input Invariant:** All outputs generated by AI agents—including hypotheses, parameters, indicators, and conditions—are treated as **untrusted external proposals**. They possess zero authority until validated by deterministic Python/NumPy logic.
2. **Authority Exclusion:** The LLM is **NEVER** authoritative for:
   - Position sizing calculation.
   - Stop-loss or take-profit price placement during order simulation.
   - Maximum risk limits or daily loss ceilings.
   - Bypassing, overriding, or relaxing validation policy gates.
   - Modifying backtester fill logic or slippage parameters.
   - Selecting live instruments or executing orders.
3. **Execution Gate:** No strategy generated in Phase 2 may advance to Phase 3 Paper Trading unless it passes every Tier-1 hard rejection gate, achieves $\text{WFE} \ge 0.50$, survives Monte Carlo stress tests, and achieves a Composite Strategy Score $\text{CSS} \ge 80.0$.

---

## 3. MULTI-AGENT RESEARCH ARCHITECTURE

Phase 2 decomposes quantitative research across 5 specialized agents with strict separation of concerns:

```text
                               +-----------------------------+
                               |     Historical Market Data  |
                               |    (Dukascopy Spot XAUUSD)  |
                               +--------------+--------------+
                                              |
                                              v
                               +-----------------------------+
                               |   Deterministic Feature &   |
                               |     Regime Summarizer       |
                               +--------------+--------------+
                                              |
                       +----------------------+----------------------+
                       |                                             |
                       v                                             v
        +-----------------------------+               +-----------------------------+
        |   AGENT 1: MARKET ANALYST   |               |   AGENT 2: REGIME ANALYST   |
        | - Macro Gold structure      |               | - Deterministic regime audit|
        | - Volatility distributions  |               | - Regime transitions        |
        | - Session characteristics   |               | - Recommended archetypes    |
        +--------------+--------------+               +--------------+--------------+
                       |                                             |
                       +----------------------+----------------------+
                                              |
                                              v
                               +-----------------------------+
                               | AGENT 3: STRATEGY RESEARCHER|
                               | - Formulates hypothesis     |
                               | - Emits StrategyProposal    |
                               | - Consumes IS memory ONLY   |
                               | - Adheres to AST contract   |
                               +--------------+--------------+
                                              |
                                              v
                               +-----------------------------+
                               |   Pydantic AST Validation   |
                               | (Deterministic Schema/AST)  |
                               +--------------+--------------+
                                              |
                                              v
                               +-----------------------------+
                               |  AGENT 4: QUANT VALIDATOR   |
                               | - Cascading Early-Exit Gate |
                               | - Deterministic Backtester  |
                               | - Walk-Forward Analysis     |
                               | - Monte Carlo Stress Test   |
                               +--------------+--------------+
                                              |
                                              v
                               +-----------------------------+
                               | AGENT 5: PERFORMANCE ANALYST|
                               | - Composite Strategy Score  |
                               | - Degradation Analysis      |
                               | - Strategy Leaderboard      |
                               | - Iterative Feedback Loop   |
                               +-----------------------------+
```

### Agent Detailed Specifications:

#### Agent 1 — Market Analyst
- **Role:** Quantitative Gold Market Structure Specialist.
- **Responsibilities:** Evaluates aggregated historical Gold market structure across London, New York, and Asian sessions. Identifies session opening drift, daily range expansion, and volatility clustering.
- **Input Context:** `MarketBrief` (JSON derived strictly from In-Sample data).
- **Strict Boundary:** Forbidden from generating trading orders, entry triggers, or trade signals.

#### Agent 2 — Regime Analyst
- **Role:** Quantitative Regime Specialist.
- **Responsibilities:** Consumes deterministic regime classifications from Phase 1 `MarketRegimeDetector` (`TRENDING_BULL`, `TRENDING_BEAR`, `RANGING`, `HIGH_VOLATILITY`, `LOW_VOLATILITY`, `BREAKOUT`). Analyzes persistence and Markov transition matrices.
- **Input Context:** Regime transition matrix, distribution of regime run lengths.
- **Strict Boundary:** The deterministic regime classification is 100% authoritative; the agent cannot reclassify historical bars.

#### Agent 3 — Strategy Researcher
- **Role:** Strategy Hypothesis & Logic Designer.
- **Responsibilities:** Formulates pre-registered falsifiable economic hypotheses. Emits fully structured `StrategyProposal` AST objects.
- **Input Context:** `StrategyResearchContext` (**strictly quarantined to In-Sample data**; zero OOS metrics).
- **Output:** `StrategyProposal` (Structured AST JSON).
- **Strict Boundary:** Prohibited from writing free-form Python or unapproved indicators.

#### Agent 4 — Quant Validator
- **Role:** Skeptical Quantitative Verification Officer.
- **Responsibilities:** Ingests strategy AST, executes cascading early-exit validation pipeline: In-Sample Backtest $\to$ Tier-1 Gates $\to$ Rolling WFA $\to$ Monte Carlo Stress Tests.
- **Output:** `ValidationReport` containing mathematically verified metrics.
- **Strict Boundary:** Must never accept performance claims made by the LLM. Recalculates all statistics independently using Python/NumPy.

#### Agent 5 — Strategy Evaluation & Performance Analyst
- **Role:** Portfolio Optimization & Research Teardown Specialist.
- **Responsibilities:** Calculates deterministic Composite Strategy Score (CSS). Evaluates IS vs OOS stability and parameter neighborhood flatness. Ranks candidates on the Leaderboard.
- **Output:** `EvaluationReport` and Leaderboard ranking.

---

## 4. MACHINE-READABLE STRATEGY CONTRACT (AST SCHEMA)

All strategies generated by Agent 3 must strictly conform to the following Pydantic v2 contract (`src/strategy/contracts.py`). Free-form natural language is rejected immediately.

### A. Pydantic Data Contract:

```python
from enum import Enum
from typing import Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, UUID4, model_validator


class TradeDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class InstrumentEnum(str, Enum):
    XAUUSD = "XAUUSD"
    GC_F = "GC=F"
    PAXGUSDT = "PAXGUSDT"


class TimeframeEnum(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class DataSourceEnum(str, Enum):
    DUKASCOPY = "DUKASCOPY"
    YAHOO = "YAHOO"
    DELTA_EXCHANGE = "DELTA_EXCHANGE"


class RegimeEnum(str, Enum):
    TRENDING_BULL = "TRENDING_BULL"
    TRENDING_BEAR = "TRENDING_BEAR"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    BREAKOUT = "BREAKOUT"


class IndicatorNameEnum(str, Enum):
    EMA = "EMA"
    SMA = "SMA"
    RSI = "RSI"
    MACD = "MACD"
    ATR = "ATR"
    BOLLINGER = "BOLLINGER"
    ADX = "ADX"
    DONCHIAN = "DONCHIAN"


class IndicatorSpec(BaseModel):
    alias: str = Field(..., pattern=r"^[a-z0-9_]{2,16}$")
    name: IndicatorNameEnum
    inputs: Dict[str, Union[int, float, str]]


class ParameterSpec(BaseModel):
    type: Literal["INT", "FLOAT"]
    default: float
    min_value: float
    max_value: float
    step: float

    @model_validator(mode="after")
    def validate_bounds(self):
        if not (self.min_value <= self.default <= self.max_value):
            raise ValueError(f"Default {self.default} out of bounds [{self.min_value}, {self.max_value}]")
        if self.step <= 0:
            raise ValueError("Step must be positive")
        return self


class ValueRef(BaseModel):
    type: Literal["PRICE", "INDICATOR", "CONSTANT"]
    name: Optional[str] = Field(None, pattern=r"^[a-z0-9_]+$")
    value: Optional[float] = None
    lag: int = Field(default=0, ge=0, le=20)

    @model_validator(mode="after")
    def validate_ref(self):
        if self.type in ("PRICE", "INDICATOR") and not self.name:
            raise ValueError(f"Field 'name' is required when type is {self.type}")
        if self.type == "CONSTANT":
            if self.value is None:
                raise ValueError("Field 'value' is required when type is CONSTANT")
            if self.lag != 0:
                raise ValueError("Constants cannot have a non-zero lag")
        return self


class ConditionComparison(BaseModel):
    left: ValueRef
    comparator: Literal[">", "<", ">=", "<=", "==", "CROSS_OVER", "CROSS_UNDER"]
    right: ValueRef


class ConditionNode(BaseModel):
    operator: Literal["AND", "OR"]
    conditions: List[ConditionComparison] = Field(..., min_length=1, max_length=5)


class StopLossSpec(BaseModel):
    type: Literal["ATR_MULTIPLE", "FIXED_POINTS", "TRAILING_ATR"]
    multiplier_or_value: float = Field(..., ge=0.5, le=10.0)


class TakeProfitSpec(BaseModel):
    type: Literal["ATR_MULTIPLE", "FIXED_POINTS", "RISK_REWARD_RATIO"]
    multiplier_or_value: float = Field(..., ge=1.0, le=20.0)


class ExecutionConstraints(BaseModel):
    max_holding_bars: int = Field(default=100, ge=1, le=500)
    allowed_sessions: List[Literal["LONDON", "NEW_YORK", "ASIAN", "ALL"]] = Field(
        default=["ALL"]
    )


class StrategyProposal(BaseModel):
    strategy_id: UUID4
    name: str = Field(..., pattern=r"^[A-Z0-9_]{3,32}$")
    hypothesis: str = Field(..., min_length=20, max_length=500)
    direction: TradeDirection  # Explicit Trade Direction (P1-1 Resolution)
    instrument: InstrumentEnum
    timeframe: TimeframeEnum
    data_source: DataSourceEnum
    target_regimes: List[RegimeEnum] = Field(..., min_length=1)
    indicators: List[IndicatorSpec] = Field(..., min_length=1, max_length=6)
    parameters: Dict[str, ParameterSpec] = Field(..., max_length=8)
    entry_rules: ConditionNode
    exit_rules: ConditionNode
    stop_loss: StopLossSpec
    take_profit: TakeProfitSpec
    constraints: ExecutionConstraints
    rationale: str = Field(..., min_length=20)
    generated_by: str = Field(default="Agent-3-StrategyResearcher")
```

---

### B. Explicit Trade Direction Semantics (P1-1 Resolution)

Every `StrategyProposal` must specify `direction: TradeDirection` (`LONG` or `SHORT`). Trading mechanics are interpreted deterministically:

1. **Entry Rules Interpretation:**
   - **LONG:** When `entry_rules` evaluates to `True` at bar $T$ Close, the engine submits a Long entry order for execution at bar $T+1$ Open.
   - **SHORT:** When `entry_rules` evaluates to `True` at bar $T$ Close, the engine submits a Short entry order for execution at bar $T+1$ Open.
2. **Exit Rules Interpretation:**
   - **LONG:** When `exit_rules` evaluates to `True`, an active Long position is closed via market sell before SL/TP hit.
   - **SHORT:** When `exit_rules` evaluates to `True`, an active Short position is closed via market buy to cover before SL/TP hit.
3. **Stop-Loss & Take-Profit Placement:**
   - **LONG:** Stop Loss placed strictly **below** entry ($P_{\text{entry}} - \text{SL}$); Take Profit strictly **above** entry ($P_{\text{entry}} + \text{TP}$).
   - **SHORT:** Stop Loss placed strictly **above** entry ($P_{\text{entry}} + \text{SL}$); Take Profit strictly **below** entry ($P_{\text{entry}} - \text{TP}$).
4. **Risk-to-Reward ($R:R$) Formula:**
   - $\text{LONG: } R:R = \frac{P_{\text{TP}} - P_{\text{entry}}}{P_{\text{entry}} - P_{\text{SL}}} \ge 1.5$
   - $\text{SHORT: } R:R = \frac{P_{\text{entry}} - P_{\text{TP}}}{P_{\text{SL}} - P_{\text{entry}}} \ge 1.5$
5. **Slippage & Half-Spread Application:**
   - **LONG Execution:** Fill Entry $= P_{\text{open, } T+1} + \frac{\text{Spread}}{2} + \text{Slippage}$; Fill Exit $= P_{\text{close, exit}} - \frac{\text{Spread}}{2} - \text{Slippage}$.
   - **SHORT Execution:** Fill Entry $= P_{\text{open, } T+1} - \frac{\text{Spread}}{2} - \text{Slippage}$; Fill Exit $= P_{\text{close, exit}} + \frac{\text{Spread}}{2} + \text{Slippage}$.
6. **Max Holding Period:** Applies identically: if active bars $\ge \text{max\_holding\_bars}$ and neither SL nor TP is triggered, liquidated on next bar open with `ExitReason.EXPIRY`.
7. **BacktestEngine Interface:** For `LONG`, entry emits `OrderSide.BUY` and exit emits `OrderSide.SELL`. For `SHORT`, entry emits `OrderSide.SELL` and exit emits `OrderSide.BUY`.
8. **Bi-Directional Systems:** Proposals are strictly uni-directional. Bi-directional trading systems are composed of two paired, independently evaluated proposals (e.g. `XAU_TREND_LONG` and `XAU_TREND_SHORT`).

---

### C. AST Lag / Lookback Syntax (P1-2 Resolution)

1. **Lag Definition:** `lag = 0` (current evaluated bar $T$), `lag = 1` (previous bar $T-1$), `lag = 2` (bar $T-2$).
2. **Bounds:** $0 \le \text{lag} \le 20$. Negative lags ($\text{lag} < 0$) are rejected by Pydantic validation, completely preventing future lookahead leakage.
3. **Missing Historical Data:** If bar index $T < \text{warmup} + \text{lag}$, comparison evaluates strictly to `False`.
4. **Indicator Calculation Order:** Indicators are computed on the full series first, then shifted by `lag`.

#### Concrete Examples:

##### Valid AST Example (LONG Breakout Confirmation):
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
      "left": { "type": "INDICATOR", "name": "rsi_14", "lag": 1 },
      "comparator": ">",
      "right": { "type": "CONSTANT", "value": 50.0, "lag": 0 }
    }
  ]
}
```

##### Invalid AST Example (Rejected: Negative Lag / Future Lookahead):
```json
{
  "left": { "type": "PRICE", "name": "close", "lag": -1 },
  "comparator": ">",
  "right": { "type": "PRICE", "name": "open", "lag": 0 }
}
```
*Rejection:* `lag: -1` violates `ge=0` constraint.

---

## 5. CONTEXT & TOKEN EFFICIENCY PRINCIPLES

1. **Raw Level (Local Parquet):** Full tick and 1-minute historical series stored in `data/normalized/` (never passed to LLM).
2. **Aggregated Feature Level (Python/NumPy):** Deterministic extraction of hourly ATR, volume distributions, session volatility, and regime state transitions.
3. **Statistical Profile Level (JSON Payload):** Condensed summary matrix generated by `MarketFeatureSummarizer` ($\approx 1,500$ to $3,500$ tokens per prompt).

---

## 6. LLM PROVIDER ABSTRACTION

All LLM communication is decoupled behind the `LLMProvider` interface (`src/ai/providers/base.py`):
- **`OllamaProvider`:** Primary / Default (`deepseek-r1:8b`, `qwen2.5-coder:7b`) via `http://localhost:11434` ($0.00 marginal cost, 100% private, offline).
- **`GeminiProvider`:** Secondary / Fallback (`gemini-2.5-flash`) via free-tier REST client.
- **`MockLLMProvider`:** Deterministic offline mock for testing.
- **Resilience:** 30s timeout, max 2 retries with error diff injection, circuit breaker on 3 consecutive failures.

---

## 7. AI RESEARCH MEMORY & OOS QUARANTINE (P1-3 RESOLUTION)

Research memory is partitioned into two mutually exclusive visibility domains to prevent indirect data snooping:

```text
+---------------------------------------------------------------------------------------------------+
| TWO-TIER RESEARCH MEMORY PARTITIONS                                                               |
+-------------------------------------------------+-------------------------------------------------+
| 1. IS_RESEARCH_MEMORY (Visible to AI Ideation)  | 2. OOS_VALIDATION_MEMORY (Strictly Quarantined) |
+-------------------------------------------------+-------------------------------------------------+
| - Pre-registered economic hypotheses            | - Out-of-Sample Sharpe, GPF, and Sortino metrics|
| - Proposed strategy ASTs & parameter bounds     | - Out-of-Sample equity curves & drawdown paths  |
| - In-Sample backtest metrics & gate failures    | - Walk-Forward fold-by-fold OOS returns         |
| - Parameter search ranges & historical plateaus | - Monte Carlo simulated failure paths           |
| - In-Sample regime breakdown statistics         | - Out-of-Sample trade execution logs            |
+-------------------------------------------------+-------------------------------------------------+
```

### Agent Memory Access Invariant:
- **Strategy Researcher (Agent 3):** Can query `IS_RESEARCH_MEMORY` ONLY. **CANNOT ACCESS `OOS_VALIDATION_MEMORY`.**
- **Typed Context Enforcement:** Prompts receive strictly `StrategyResearchContext`:
  ```python
  class StrategyResearchContext(BaseModel):
      session_id: UUID4
      hypothesis_id: UUID4
      thesis_statement: str
      target_regime: RegimeEnum
      market_brief: MarketBrief
      is_diagnostic_history: List[ISDiagnosticRecord]
      parameter_plateaus: List[ParameterPlateauRecord]
      # OOS metrics, curves, and trade records are STRUCTURALLY ABSENT from this model!
  ```
- **Database & API Query Filters:** Queries for strategy ideation enforce `WHERE partition = 'IN_SAMPLE'`.

---

## 8. EXTENDED STRATEGY LIFECYCLE

```text
[PHASE 2 SCOPE]
RESEARCH (Hypothesis formulated)
    │
    ▼
PROPOSED (AST Strategy proposal generated)
    │
    ▼
SCHEMA_VALIDATED (Pydantic / AST validation passed)
    │
    ▼
BACKTESTED (Deterministic In-Sample backtest completed)
    │
    ▼
VALIDATED (Walk-Forward Analysis completed & WFE verified)
    │
    ▼
OOS_PASSED (Monte Carlo and holdout stress tests passed)
    │
    ▼
ACCEPTED_CANDIDATE (Composite Strategy Score CSS >= 80.0) ────────┐
    │                                                            │
    ▼ (Failed any Gate)                                          │
REJECTED (Archived with immutable rejection reason)              │
                                                                 │
=================================================================│====================
[PHASE 3 SCOPE - FROZEN IN PHASE 2]                              │
PAPER_TRADING <──────────────────────────────────────────────────┘
```

Phase 2 **STOPS** at `ACCEPTED_CANDIDATE` or `REJECTED`. It is physically incapable of promoting a strategy into `PAPER_TRADING`.

---

## 9. CONFIGURABLE VALIDATION POLICIES (P2-1 RESOLUTION)

Validation policies are maintained as configurable parameters in `src/core/config.py` with institutional defaults:

```text
+---+------------------------------+---------+-----------------------+---------------------------------------+
| # | Policy Parameter             | Default | Nature Classification | Economic / Statistical Rationale      |
+---+------------------------------+---------+-----------------------+---------------------------------------+
| 1 | MIN_TRADES_GATE              | 50      | Research Heuristic    | Sample size power for central limit   |
| 2 | MIN_EXPECTANCY_SPREAD_RATIO  | 0.15    | Research Heuristic    | Edge exceeds transaction friction     |
| 3 | MIN_GROSS_PROFIT_FACTOR      | 1.30    | Research Heuristic    | Eliminates marginal / breakeven sets  |
| 4 | MAX_DRAWDOWN_GATE            | 0.15    | Risk Invariant        | Bounds peak-to-trough capital loss    |
| 5 | MAX_CONSECUTIVE_LOSSES       | 8       | Research Heuristic    | Bounds adverse psychological streaks  |
| 6 | MIN_OOS_RETENTION_RATIO      | 0.65    | Research Heuristic    | Disqualifies curve-fitted decay       |
| 7 | MAX_RUIN_PROBABILITY         | 0.05    | Risk Invariant        | Max acceptable probability of 20% DD  |
| 8 | MIN_WFE_RATIO                | 0.50    | Research Heuristic    | Walk-forward consistency standard     |
| 9 | MIN_STRESS_PROFIT_FACTOR     | 1.10    | Risk Invariant        | Margin of safety under +50% friction  |
| 10| MAX_PARAM_DEGRADATION        | 0.40    | Research Heuristic    | Disqualifies knife-edge spikes        |
| 11| CSS_TIER1_THRESHOLD          | 80.0    | Configurable Policy   | Prime candidate Phase 3 eligibility   |
| 12| CSS_TIER2_THRESHOLD          | 70.0    | Configurable Policy   | Reserve candidate threshold           |
+---+------------------------------+---------+-----------------------+---------------------------------------+
```

### Policy Authority Invariant:
- Validation policies can be modified **strictly by the Human Operator** via `.env`.
- **AI agents are STRICTLY FORBIDDEN from modifying or overriding validation policies.**

---

## 10. WALK-FORWARD ANALYSIS & WFE MATHEMATICAL FORMULATION (P1-4 RESOLUTION)

### Rolling Window Geometry:
- **Training Window (IS):** 12 months rolling.
- **Optimization Window:** 3 months rolling.
- **Out-of-Sample Window (OOS):** 3 months forward-anchored. Step size: 3 months.

### Walk-Forward Efficiency (WFE) Formulation:
WFE is represented uniformly across the entire system as a **decimal ratio** in $[0.0, \infty)$ (where `0.5000` is the default passing threshold):

```text
IF R_IS <= 0.0:
    WFE = 0.0000
    WFE_GATE = FAIL
    (Rationale: Losing or breakeven In-Sample strategies cannot be evaluated for forward efficiency).

ELSE IF R_OOS <= 0.0 OR N_trades_OOS == 0:
    WFE = 0.0000
    WFE_GATE = FAIL
    (Rationale: Strategy failed in the forward out-of-sample holdout).

ELSE:
    WFE = round(R_OOS / R_IS, 4)
    IF WFE >= MIN_WFE_RATIO (0.5000):
        WFE_GATE = PASS
    ELSE:
        WFE_GATE = FAIL
```

#### Deterministic Edge Case Verification:
- $R_{\text{IS}} = +0.20, R_{\text{OOS}} = +0.10 \implies \text{WFE} = \mathbf{0.5000} \implies$ **PASS**
- $R_{\text{IS}} = 0.00 \implies \mathbf{WFE = 0.0000} \implies$ **FAIL** (no division by zero)
- $R_{\text{IS}} = -0.10, R_{\text{OOS}} = -0.05 \implies \mathbf{WFE = 0.0000} \implies$ **FAIL** (anti-false-positive guard)
- $R_{\text{IS}} = +0.20, R_{\text{OOS}} = -0.05 \implies \mathbf{WFE = 0.0000} \implies$ **FAIL**
- $R_{\text{IS}} = +0.20, R_{\text{OOS}} = +0.12 \implies \text{WFE} = \mathbf{0.6000} \implies$ **PASS**

---

## 11. MONTE CARLO & STRESS TESTING SPECIFICATION

1. **Trade Sequence Reshuffling (Bootstrap):** 2,000 synthetic paths sampled with replacement; calculates $\text{MaxDD}_{95\%}$ and ruin probability ($\mathbb{P}(\text{DD} \ge 20\%) < 0.05$).
2. **Execution Friction Perturbation:** Re-evaluates trade fills with $+50\%$ slippage and spread; strategy must maintain $\text{GPF} \ge 1.10$.
3. **Parameter Neighborhood Stability Test:** Perturbs continuous parameters by $\pm 10\%$; performance drop $> 40\%$ triggers knife-edge rejection.
4. **Scope:** Evaluates path-independent tail risk; **does NOT predict future profitability.**

---

## 12. COMPOSITE STRATEGY SCORING (CSS)

Every strategy surviving all prior validation gates receives a deterministic score from $0.0$ to $100.0$:

$$\text{CSS} = \left( \sum_{i=1}^{6} w_i \cdot S_i \right) \times 100$$

$$\begin{aligned}
S_1 &= \min\left(1.0, \max\left(0.0, \frac{\text{Sortino}_{\text{OOS}}}{3.0}\right)\right) \quad &(w_1 = 0.25) \\
S_2 &= \max\left(0.0, 1.0 - \frac{\text{MaxDD}_{\text{OOS}}}{0.15}\right) \quad &(w_2 = 0.20) \\
S_3 &= \min\left(1.0, \max\left(0.0, \frac{\text{GPF}_{\text{OOS}} - 1.0}{1.5}\right)\right) \quad &(w_3 = 0.15) \\
S_4 &= \min\left(1.0, \max\left(0.0, \frac{\text{WFE}}{1.0}\right)\right) \quad &(w_4 = 0.15) \\
S_5 &= \max\left(0.0, 1.0 - \frac{\text{MaxDD}_{95\%}}{0.20}\right) \quad &(w_5 = 0.15) \\
S_6 &= \max\left(0.0, 1.0 - \frac{\text{Degradation}_{10\%}}{0.40}\right) \quad &(w_6 = 0.10)
\end{aligned}$$

- **$\text{CSS} \ge 80.0$:** Prime Candidate (Phase 3 Eligible).
- **$70.0 \le \text{CSS} < 80.0$:** Reserve Candidate.
- **$\text{CSS} < 70.0$:** Disqualified.
- **Hard Gate Veto:** CSS is computed strictly after all hard gates pass. CSS cannot resurrect a rejected strategy.

---

## 13. ANTI-OVERFITTING & CASCADING EARLY-EXIT PIPELINE (P2-3 RESOLUTION)

Validation executes as a strict cascading early-exit pipeline to optimize compute and prevent data snooping:

```text
Step 1: AST Schema Validation (0 CPU)
   ├── FAIL ──► ABORT & Request 1 Correction Attempt
   └── PASS
        ▼
Step 2: In-Sample (IS) Deterministic Backtest (~100ms)
        ▼
Step 3: In-Sample Hard Gate Evaluation (~1ms)
   ├── Checks: Trade Count >= 50, Expectancy >= 0.15*Spread, GPF >= 1.30, MaxDD <= 15%, Streak <= 8
   ├── FAIL ──► REJECT IMMEDIATELY. DO NOT RUN WFA. DO NOT RUN MONTE CARLO.
   └── PASS
        ▼
Step 4: Walk-Forward Analysis (WFA) (1-3s)
   ├── Checks: WFE >= 0.50, GPF_OOS / GPF_IS >= 0.65
   ├── FAIL ──► REJECT IMMEDIATELY. DO NOT RUN MONTE CARLO.
   └── PASS
        ▼
Step 5: Monte Carlo Stress Testing (2-5s)
   ├── 2,000 bootstrap order reshuffles + friction stress (+50% spread/slippage)
   ├── Checks: Ruin Prob < 5%, Stress GPF >= 1.10, Param Degradation <= 40%
   ├── FAIL ──► REJECT.
   └── PASS
        ▼
Step 6: Composite Strategy Score (CSS) Calculation
   └── Computes final score and ranks candidate on Leaderboard.
```

---

## 14. CRYPTOGRAPHIC AI AUDIT TRAIL

Every agent decision is recorded in `agent_decisions`:
- `id` (UUID PK), `session_id` (UUID FK), `agent_name`, `provider`, `model`.
- `prompt_hash`: SHA-256 hex string of the exact sanitized prompt.
- `prompt_tokens`, `completion_tokens`, `input_payload`, `output_payload`.
- `decision_type`: `HYPOTHESIS_FORMULATED`, `STRATEGY_PROPOSED`, `VALIDATION_EXECUTED`, `GATE_EVALUATED`, `STRATEGY_SCORED`.
- `decision_outcome`: `PASS`, `FAIL`, `RETRY`, `TERMINATE`.
- `rationale`: Text, `created_at`: UTC Timestamp.

---

## 15. DATABASE DESIGN & SQLITE BATCH MIGRATION (ADR-011 & P3-2 RESOLUTION)

### Activated Phase 2 Tables:
`validation_results`, `walk_forward_results`, `monte_carlo_results`, `agent_decisions`.

### New Phase 2 Tables:
`research_sessions`, `research_hypotheses`.

### SQLite Parity Mandate (P3-2 Resolution):
Alembic migration `002_phase2_research_tables.py` adding `strategy_versions.hypothesis_id` must use batch mode:
```python
with op.batch_alter_table("strategy_versions") as batch_op:
    batch_op.add_column(sa.Column("hypothesis_id", sa.String(36), nullable=True))
    batch_op.create_foreign_key(
        "fk_strategy_versions_hypothesis",
        "research_hypotheses",
        ["hypothesis_id"],
        ["id"],
        ondelete="SET NULL",
    )
```

---

## 16. REST API WITH ASYNC JOB ABSTRACTION (P2-2 RESOLUTION)

To prevent HTTP gateway timeouts during multi-fold WFA and 2,000-path Monte Carlo simulations, validation endpoints use an asynchronous job polling abstraction:

| Method | Endpoint | Description | Behavior | Response Payload |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/research/sessions` | Create research session | Synchronous | `ResearchSessionResponse` |
| `GET` | `/api/v1/research/sessions/{id}`| Get session status | Synchronous | `ResearchSessionDetailResponse`|
| `POST` | `/api/v1/research/sessions/{id}/hypotheses` | Pre-register hypothesis | Synchronous | `HypothesisResponse` |
| `POST` | `/api/v1/research/sessions/{id}/generate-strategy` | Trigger AI generation | Synchronous | `StrategyProposalResponse` |
| `POST` | `/api/v1/research/strategies/{id}/validate` | In-Sample Hard Gates | Synchronous | `ValidationResultResponse` |
| `POST` | `/api/v1/research/strategies/{id}/walk-forward` | Submit rolling WFA job | **Asynchronous (202 Accepted)** | `{"job_id": "...", "status": "PENDING"}` |
| `POST` | `/api/v1/research/strategies/{id}/monte-carlo` | Submit Monte Carlo job | **Asynchronous (202 Accepted)** | `{"job_id": "...", "status": "PENDING"}` |
| `GET` | `/api/v1/research/jobs/{job_id}` | Poll job status | Synchronous | `JobStatusResponse (PENDING, RUNNING, COMPLETED, FAILED)` |
| `GET` | `/api/v1/research/jobs/{job_id}/result` | Fetch completed job results | Synchronous | `ValidationJobResultResponse` |
| `GET` | `/api/v1/research/strategies/{id}/score` | Compute Composite Score | Synchronous | `StrategyScoreResponse` |
| `GET` | `/api/v1/research/leaderboard` | Ranked strategy candidates | Synchronous | `LeaderboardResponse` |
| `GET` | `/api/v1/research/audit/{session_id}`| Query audit trail | Synchronous | `AgentDecisionListResponse` |

---

## 17. COST & SECURITY MODEL (P3-1 RESOLUTION)

1. **Free-First Hierarchy:** Local computation $\to$ Local Ollama ($0.00) $\to$ Gemini free tier ($0.00).
2. **Defense-in-Depth Prompt Construction (P3-1 Resolution):** Prompts use typed slot-filling templates (`PromptTemplate`). Raw string concatenation of external user input into system prompts is strictly forbidden.
3. **AST Execution Isolation:** Strategy rules are strictly evaluated via pre-compiled AST logic; zero `eval()`, `exec()`, or Python code interpretation from LLM strings.
4. **Zero Live Credentials:** `LIVE_TRADING_ENABLED=false` remains locked.

---

## 18. PHASE 2 TESTING STRATEGY

Achieves $\ge 85\%$ test coverage across 4 distinct testing modalities:
1. **Unit Tests (`tests/unit/ai/`):** AST validation, `direction` parsing, lag indexing ($0 \le \text{lag} \le 20$), `MockLLMProvider`, WFE edge cases (all 5 deterministic examples), CSS formula precision.
2. **Integration Tests (`tests/integration/ai/`):** Multi-agent orchestration, cascading early-exit pipeline, async validation job worker, `StrategyResearchContext` OOS quarantine assertion.
3. **Adversarial Tests (`tests/adversarial/`):** Malformed JSON, prompt injection, hallucinated indicators, inverted SL/TP, negative lag rejection, provider timeout and circuit breaker.
4. **Determinism Tests (`tests/determinism/`):** Bit-for-bit reproducibility of identical strategy ASTs on identical Parquet data across seed-pinned Monte Carlo and WFA folds.

---

## 19. PHASE 2 IMPLEMENTATION SEQUENCE ROADMAP

```text
Workstream 1: Contracts & AST Interpreter
├── Implement `src/strategy/contracts.py` (Pydantic AST with direction & ValueRef)
└── Implement `src/strategy/interpreter.py` (Deterministic AST to Vectorized Signal engine)

Workstream 2: LLM Provider Abstraction
├── Implement `src/ai/providers/base.py` (Abstract interface)
├── Implement `src/ai/providers/ollama.py` (Ollama REST client)
├── Implement `src/ai/providers/gemini.py` (Gemini REST client)
└── Implement `src/ai/providers/mock.py` (Deterministic test mock)

Workstream 3: Context & Feature Summarizer with OOS Quarantine
└── Implement `src/ai/summarizer.py` (Condensed statistical profile builder + StrategyResearchContext)

Workstream 4: Multi-Agent Collective
├── Implement `src/ai/agents/market_analyst.py`
├── Implement `src/ai/agents/regime_analyst.py`
├── Implement `src/ai/agents/strategy_researcher.py`
├── Implement `src/ai/agents/quant_validator.py`
└── Implement `src/ai/agents/performance_analyst.py`

Workstream 5: Walk-Forward, Monte Carlo & Early-Exit Pipeline
├── Implement `src/quant/validation/walk_forward.py` (Robust WFE ratio)
├── Implement `src/quant/validation/monte_carlo.py` (2,000-path bootstrap)
└── Implement `src/quant/validation/pipeline.py` (Cascading early-exit gatekeeper)

Workstream 6: Strategy Scoring & Leaderboard
└── Implement `src/quant/scoring.py` (Composite Strategy Score calculator)

Workstream 7: Database Migration & Persistence
├── Alembic batch migration `002_phase2_research_tables.py`
└── SQLAlchemy models for `research_sessions` and `research_hypotheses`

Workstream 8: Phase 2 REST API Endpoints with Async Jobs
└── FastAPI router `src/api/routes/research.py` (Job polling for WFA/MC)

Workstream 9: Comprehensive Test Suite & Verification Audit
└── Run full test suite, verify >= 85% coverage, zero Phase 1 regressions
```

---

## 20. ARCHITECTURAL ACCEPTANCE CRITERIA

Phase 2 will be deemed complete and ready for audit only when:
1. All 5 AI agents successfully execute in sequence using the primary local provider (`OllamaProvider`) or mock fixture.
2. 100% of candidate strategies are validated through deterministic cascading early-exit gates, WFA, and Monte Carlo stress tests.
3. Zero raw candle arrays or Out-of-Sample metrics are transmitted to LLM prompts.
4. Zero arbitrary code execution paths exist.
5. All agent decisions and prompt hashes are permanently recorded in PostgreSQL.
6. The test suite passes with $\ge 85\%$ coverage and 0 regressions against Phase 1.
7. Phase 3 (Paper Trading / React) and Phase 4 (Live Trading) boundaries remain 100% untouched.
