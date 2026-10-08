# GOLD AI TRADING AGENT — SYSTEM ARCHITECTURE
*Institutional-Grade 4-Phase Modular Architecture for Gold / XAUUSD & Delta Exchange*

---

## 1. ARCHITECTURAL OVERVIEW & DESIGN PRINCIPLES

The Gold AI Trading Agent is designed as a modular, decoupled quantitative research and trading system. It enforces strict separation between statistical computation, AI hypothesis generation, deterministic risk verification, and exchange execution.

```text
+---------------------------------------------------------------------------------------------------+
|                                      DATA INGESTION & PIPELINE (Phase 1)                          |
|  +------------------------+   +-----------------------+   +---------------------+                 |
|  | Dukascopy XAUUSD       |   | Yahoo Finance GC=F    |   | Delta Exchange REST |                 |
|  | (Primary Research Data)|   | (Secondary/Futures)   |   | (Execution Venue)   |                 |
|  +-----------+------------+   +-----------+-----------+   +----------+----------+                 |
|              |                            |                          |                            |
|              +----------------------------+--------------------------+                            |
|                                           v                                                       |
|                              +------------------------+                                           |
|                              | Raw Storage (data/raw) | (Immutable, Checksummed)                  |
|                              +-----------+------------+                                           |
|                                           v                                                       |
|                              +------------------------+                                           |
|                              | Data Quality Engine    | --> Quality Reports & Quarantined Data    |
|                              +-----------+------------+                                           |
|                                           v                                                       |
|                              +------------------------+                                           |
|                              |  Normalized Parquet    | (data/normalized, data/timeframe)         |
|                              +-----------+------------+                                           |
+-------------------------------------------|-------------------------------------------------------+
                                            v
+---------------------------------------------------------------------------------------------------+
|                                 QUANTITATIVE ANALYTICS & REGIME (Phase 1)                         |
|  +--------------------+   +-----------------------+   +--------------------+                      |
|  | Technical Features |   | Multi-Timeframe Sync  |   | Market Regime      |                      |
|  | (ATR, RSI, BB,...) |   | (1m -> 5m, 15m, 1h, 4h|   | (Trend, Range, Vol,|                      |
|  +--------------------+   |  and 1d UTC bars)     |   |  Abnormal Spikes)  |                      |
|                           +-----------------------+   +--------------------+                      |
+-------------------------------------------|-------------------------------------------------------+
                                            v
+---------------------------------------------------------------------------------------------------+
|                             AI STRATEGY FACTORY & VALIDATION (Phase 2)                            |
|  +--------------------+   +-----------------------+   +--------------------+                      |
|  | Market Analyst     |   | Strategy Researcher   |   | Robustness Suite   |                      |
|  | (Structure/Regime) |   | (Hypothesis Generator)|   | (WFA, Monte Carlo, |                      |
|  +--------------------+   +-----------+-----------+   |  OOS Gate, Pine)   |                      |
|                                                       +--------------------+                      |
+-------------------------------------------|-------------------------------------------------------+
                                            v
+---------------------------------------------------------------------------------------------------+
|                             DETERMINISTIC BACKTESTING ENGINE (Phase 1)                            |
|  +--------------------+   +-----------------------+   +--------------------+                      |
|  | Bar-by-Bar Matcher |   | Realism Cost Engine   |   | Quantitative       |                      |
|  | (Zero Lookahead)   |   | (Spread, Slip, Fees)  |   | Metrics Engine     |                      |
|  +--------------------+   +-----------------------+   +--------------------+                      |
+-------------------------------------------|-------------------------------------------------------+
                                            v
+---------------------------------------------------------------------------------------------------+
|                             DETERMINISTIC RISK & EXECUTION PIPELINE                               |
|                                                                                                   |
|     Strategy Signal                                                                               |
|            |                                                                                      |
|            v                                                                                      |
|   +-------------------+       REJECTED                                                            |
|   | Deterministic     | -------------------> [ Signal Dropped / Audit Log ]                       |
|   | Risk Engine       | (Phase 3)                                                                 |
|   +--------+----------+                                                                           |
|            | APPROVED                                                                             |
|            v                                                                                      |
|   +-------------------+                                                                           |
|   | Kill Switch Gate  | ===> EMERGENCY HALT IF DRAWDOWN / LATENCY / SPREAD BREACHED (Phase 3)     |
|   +--------+----------+                                                                           |
|            | PASSED                                                                               |
|            v                                                                                      |
|   +-------------------+                                                                           |
|   | Execution Engine  |                                                                           |
|   +--------+----------+                                                                           |
|            |                                                                                      |
|            +------------------------------------+                                                 |
|            | (Phase 3)                          | (Phase 4)                                       |
|            v                                    v                                                 |
|   +-------------------+                +-------------------+                                      |
|   | Paper Trading Sim |                | Delta Adapter     | (LIVE_TRADING_ENABLED=false default) |
|   | & Trade Journal   |                | Exchange Client   |                                      |
|   +-------------------+                +-------------------+                                      |
+---------------------------------------------------------------------------------------------------+
```

---

## 2. SEPARATION OF INSTRUMENT DOMAINS

A foundational invariant of this platform is that **different Gold instruments must never be conflated or silently substituted**.

| Instrument Domain | Primary Symbol | Source Venue | Role & Characteristics | Phase Introduced |
| :--- | :--- | :--- | :--- | :--- |
| **Primary Research Gold** | `XAUUSD` | Dukascopy | Spot Gold (1 oz USD). Continuous high-liquidity interbank pricing. Free tick/minute history. Primary baseline for quant research. | Phase 1 |
| **Secondary Research Gold** | `GC=F` | COMEX / Yahoo Finance | Continuous COMEX Gold Futures (100 oz contract). Used for macro cross-validation and futures market structural comparison. | Phase 1 |
| **Execution Gold (Derivatives)**| `PAXGUSDT` / `XAUUSD_PERP` | Delta Exchange | Crypto-collateralized gold perpetual/futures contracts. Quotes and settles in USDT; tick size, multiplier, fees, and funding mechanism differ from spot gold. | Discovered in Phase 1, Executed in Phase 3 & 4 |

### Instrument Provenance Rule
All storage paths, database rows, cache keys, and backtest configurations **MUST** record the tuple `(source, instrument, timeframe)`. An algorithm evaluated on `(Dukascopy, XAUUSD)` must never be reported as evaluated on `(Delta, PAXGUSDT)` or `(Yahoo, GC=F)`.

---

## 3. MODULAR BOUNDARIES & PACKAGE RESPONSIBILITIES

```text
src/
├── core/                   # [Phase 1] System configuration, logging, events, exceptions, base types
│   ├── config.py           # Pydantic BaseSettings (env vars, secrets, thresholds)
│   ├── logger.py           # Structured JSON / console logging with redaction
│   ├── events.py           # In-process event bus for messaging and audit log
│   └── constants.py        # Canonical symbols, timeframes, market sessions
│
├── data/                   # [Phase 1] Data Ingestion, Cleaning, and Storage
│   ├── sources/            # Ingestion connectors (Dukascopy primary, Yahoo secondary, Delta, Local)
│   ├── validation/         # Quality checks (NaN, OHLC logic, gaps, spikes, monotonic order)
│   ├── storage/            # Parquet file managers, metadata registry with SHA-256
│   └── resampler.py        # Multi-timeframe resampling (1m -> 5m, 15m, 30m, 1h, 4h, 1d)
│
├── quant/                  # [Phase 1] Quantitative Analysis & Features
│   ├── indicators/         # Deterministic indicators (ATR, RSI, EMA, BB, MACD, ADX, Donchian)
│   ├── regime/             # Market regime classifier (Trending, Ranging, High Vol, Abnormal)
│   └── sessions.py         # Gold market session identification (Asian, London, NY, Overlap)
│
├── backtest/               # [Phase 1] Deterministic Backtesting Engine
│   ├── engine.py           # Bar-by-bar matching engine with zero lookahead (close & next open fills)
│   ├── execution.py        # Fill models: spread, slippage, commission, conservative intra-bar SL/TP
│   ├── metrics.py          # Complete quantitative performance metrics (Sharpe, Sortino, MaxDD, etc.)
│   ├── position.py         # Position tracking, margin, unrealized/realized P&L
│   └── types.py            # Backtest orders, fills, trade log definitions
│
├── strategy/               # [Phase 1] Strategy Definition and Repository
│   ├── base.py             # BaseStrategy abstract base class
│   ├── parameters.py       # Parameter schema, ranges, mutation rules
│   ├── registry.py         # Versioned strategy catalogue
│   └── strategies/         # Concrete reference implementations (EMA, ATR Breakout, RSI Mean Rev)
│
├── validation/             # [Phase 2] Overfitting & Robustness Engine
│   ├── walk_forward.py     # Rolling window walk-forward analyzer
│   ├── monte_carlo.py      # Resampling, permutations, ruin probability
│   ├── out_of_sample.py    # Holdout partition testing gate
│   └── scoring.py          # Composite strategy robustness score
│
├── ai/                     # [Phase 2] AI Strategy Factory & Agents
│   ├── provider.py         # LLM Provider abstraction (Ollama primary, Gemini secondary)
│   ├── analyst.py          # Market structure and regime reasoning
│   ├── researcher.py       # Strategy hypothesis generator
│   ├── evaluator.py        # Performance & failure diagnosis agent
│   └── pine_transpiler.py  # Pine Script v5 generator with parity verification
│
├── risk/                   # [Phase 3] Deterministic Risk Management
│   ├── engine.py           # Absolute risk rules (max daily loss, max exposure, drawdown limits)
│   ├── sizing.py           # Position sizing (fixed fractional, volatility-adjusted)
│   └── kill_switch.py      # Emergency kill switch triggers & audit
│
├── execution/              # [Phase 3 & 4] Execution & Exchange Adapters
│   ├── base.py             # ExchangeAdapter abstract interface
│   ├── paper.py            # [Phase 3] Real-time simulated paper trading engine
│   └── delta/              # [Phase 3 & 4] Delta Exchange REST and WebSocket client
│
├── database/               # [Phase 1 Foundation] Relational Persistence
│   ├── connection.py       # Async SQLAlchemy session factory & engine (SQLite / PostgreSQL)
│   ├── models/             # SQLAlchemy ORM schemas with explicit phase ownership
│   └── repository.py       # Clean repository pattern for queries
│
└── api/                    # [Phase 1 Foundation] FastAPI HTTP Interface
    ├── routes/             # REST endpoints (health, system, data, strategies, backtest)
    ├── websockets/         # [Phase 3] Real-time streaming for frontend dashboard
    └── schemas.py          # Pydantic request/response schemas
```

---

## 4. CORE SUB-SYSTEM SPECIFICATIONS

### 4.1 Data Pipeline
- **Raw Data Invariant:** Every downloaded payload is written to `data/raw/{source}/{instrument}/{filename}` in immutable raw format prior to any transformation.
- **Normalization Standard:** All data frames conform to the strict schema:
  `[timestamp (UTC datetime64[ns, UTC]), open (float64), high (float64), low (float64), close (float64), volume (float64), source (str), instrument (str)]`.
- **Validation Engine:** Evaluates:
  - Non-monotonic timestamps or duplicate rows.
  - Geometry: `high < low`, `high < open`, `high < close`, `low > open`, `low > close`.
  - Non-positive or NaN values in OHLC bars.
  - Price jumps $> 5\sigma$ relative to trailing 20-bar ATR.
  - Interval gaps based on market session calendar.
- **Resampling:** Left-closed, left-labeled intervals (`[t, t + Δt)` labeled as `t`) in strict UTC. Zero lookahead. Base 1m data is used when available, but higher timeframe data may be ingested directly.

### 4.2 Backtesting Execution Model
- **Zero Lookahead:** Features, indicators, and signal logic at bar $t$ must only reference bar $t$ and historical bars $[0, \dots, t]$.
- **Execution Timing Models:**
  - Bar Close Fill: Signal generated at bar $t$ close executes at bar $t$ close with half-spread and adverse slippage.
  - Next Bar Open Fill: Signal generated at bar $t$ close executes at bar $t+1$ open.
- **Conservative Intra-Bar Path Traversal:**
  - When a bar's $[Low_t, High_t]$ range crosses both the active position's Stop Loss and Take Profit levels, the engine conservatively models that the **Stop Loss triggered first**.
- **Realistic Cost Modeling:**
  - Spread: Half-spread added to buy price, subtracted from sell price.
  - Slippage: Modeled as base tick slippage plus volatility-scaled adverse execution.
  - Commissions: Explicit maker ($0.02\%$) and taker ($0.05\%$) schedules matching exchange terms.

### 4.3 Metrics Engine
- **Standard Sortino Ratio:** Downside semi-deviation is calculated across the full sample of $N$ returns with non-negative returns clamped to zero, avoiding sample size bias from filtering only negative returns.
- **Profit Factor:** $\frac{\text{Gross Profit}}{\text{Gross Loss}}$, separating gross trading gains from losses before fee attribution.
- **Drawdown:** Comprehensive tracking of Peak-to-Trough equity drawdown (% and USD) and duration in bars.

---

## 5. INFRASTRUCTURE & DEPENDENCY BOUNDARIES

### 5.1 Redis Phase Boundary
- **Phase 1 Status:** **Zero Redis Dependency.** Phase 1 is purely a data ingestion, quantitative research, backtesting, and REST API platform. Redis is not imported or required for any Phase 1 functionality.
- **Phase 3 Introduction:** Redis 7 is introduced in Phase 3 strictly for:
  - Real-time market tick caching.
  - Paper trading order book pub/sub.
  - WebSocket streaming state management for the React dashboard.
- **Docker Compose Configuration:** In Phase 1, backend service startup must not be gated by Redis health checks.

### 5.2 AI & LLM Boundaries
- **Phase 1:** Zero LLM dependencies. All Phase 1 modules (indicators, backtest, regimes, metrics) are 100% deterministic Python/NumPy code.
- **Phase 2:** AI components operate via the `LLMProvider` abstraction. Default provider is local/self-hosted (Ollama); secondary provider is Gemini free-tier.
- **Veto & Security Gate:** Under no circumstances may an AI agent:
  - Directly place orders or interact with exchange APIs.
  - Access private keys, secrets, or API credentials.
  - Override deterministic risk management rules or kill switches.

---

## 6. DATABASE SCHEMA & PHASE OWNERSHIP

The database schema defines entities supporting the complete 4-phase roadmap. However, functional availability is partitioned as follows:

| Table Name | Phase Owner | Operational Status in Phase 1 |
| :--- | :--- | :--- |
| `instruments` | Phase 1 | **Active** (Canonical Gold instrument catalogue) |
| `market_data_metadata` | Phase 1 | **Active** (Registry of ingested datasets & quality scores) |
| `strategies` | Phase 1 | **Active** (Strategy registry records) |
| `strategy_versions` | Phase 1 | **Active** (Version history of strategy definitions) |
| `strategy_parameters` | Phase 1 | **Active** (Registered parameter schemas) |
| `backtests` | Phase 1 | **Active** (Backtest runs and summary performance) |
| `backtest_trades` | Phase 1 | **Active** (Individual simulated trade execution logs) |
| `market_regimes` | Phase 1 | **Active** (Historical market regime classifications) |
| `system_events` | Phase 1 | **Active** (System audit and lifecycle event log) |
| `validation_results` | Phase 2 | *Schema provisioned; populated during Phase 2 validation runs* |
| `walk_forward_results` | Phase 2 | *Schema provisioned; populated during Phase 2 WFA* |
| `monte_carlo_results` | Phase 2 | *Schema provisioned; populated during Phase 2 Monte Carlo* |
| `signals` | Phase 2 / 3 | *Schema provisioned; active in live/paper signal generation* |
| `agent_decisions` | Phase 2 | *Schema provisioned; stores AI hypothesis generation logs* |
| `paper_trades` | Phase 3 | *Schema provisioned; populated during Phase 3 paper trading* |
| `positions` | Phase 3 | *Schema provisioned; real-time position tracking in Phase 3* |
| `risk_events` | Phase 3 | *Schema provisioned; risk gate rejections & kill switch events* |
| `live_trades` | Phase 4 | *Schema provisioned; live exchange execution audit log* |

---

## 7. 4-PHASE ROADMAP & SUCCESS CRITERIA

```text
Phase 1: Foundation + Data Ingestion + Deterministic Backtester (VERIFIED & FROZEN v1.1.0-phase1-verified)
  ├── Canonical Governance: PROJECT_CONSTITUTION.md, ARCHITECTURE.md, PHASE_1_SPEC.md, ADR-001–005
  ├── Ingestion: Dukascopy XAUUSD (primary), Yahoo GC=F (secondary), Delta public metadata
  ├── Data Quality Engine: 9 corruption checks, NaN drop, quality reporting
  ├── Deterministic Indicators & Gold Session / Market Regime classification
  ├── Backtest Engine: Bar close & next open fills, conservative SL/TP tie-breaker, realistic costs
  ├── Institutional Metrics: Sharpe, standard Sortino, Gross Profit Factor, Drawdown, Expectancy
  ├── FastAPI Endpoints & >= 85% Unit/Integration Test Coverage (60 tests, 91% coverage)
  └── Status: 100% Verified, Certified & Frozen

Phase 2: AI Multi-Agent Strategy Generation & Validation Engine (SPECIFIED & FROZEN DESIGN)
  ├── Canonical Specification: docs/PHASE_2_SPEC.md, ADR-006 through ADR-011
  ├── 5 Specialized Agents: Market Analyst, Regime Analyst, Strategy Researcher, Quant Validator, Performance Analyst
  ├── Non-Negotiable Principle: AI Proposes, Deterministic Systems Verify, Risk Engine Vetos
  ├── LLM Provider Abstraction: Local Ollama primary (zero cost), Gemini secondary (ADR-006)
  ├── Machine-Readable Strategy Contract: Strict Pydantic AST schema (ADR-007)
  ├── Anti-Overfitting & Hard Rejection Gates: Trade count, GPF, MaxDD, DSR, 10-iteration limit (ADR-008)
  ├── Validation Engines: Rolling Walk-Forward Analysis (WFE >= 50%) & Monte Carlo Bootstrap (ADR-009)
  ├── Deterministic Ranking: Composite Strategy Score (CSS >= 80.0 for Phase 3 progression) (ADR-010)
  ├── Research Memory: PostgreSQL sessions, hypotheses, decisions + file artifacts (ADR-011)
  └── Output: Validated Strategy Catalogue & Institutional Research Teardowns

Phase 3: Paper Trading + Real-Time Risk + Dashboard
  ├── Real-time Delta WebSocket streaming & Paper Trading Engine
  ├── Deterministic Risk Engine: 8 mandatory gates & Kill Switch circuit breaker
  ├── Redis 7 caching & pub/sub bus
  ├── React 18 + TypeScript + Vite institutional trading dashboard
  └── Output: 4-week paper trading certification & Phase 3 Report

Phase 4: Delta Live Execution + Production Deployment
  ├── Authenticated Delta Exchange integration (HMAC SHA-256)
  ├── Real-time order & position reconciliation daemons
  ├── Hard-locked live trading gate (default LIVE_TRADING_ENABLED=false)
  └── Output: Production VPS Deployment & Final Operations Manual
```
