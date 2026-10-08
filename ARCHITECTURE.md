# GOLD AI TRADING AGENT — SYSTEM ARCHITECTURE
*Institutional-Grade 4-Phase Modular Architecture for Gold / XAUUSD & Delta Exchange*

---

## 1. ARCHITECTURAL OVERVIEW & DESIGN PRINCIPLES

The Gold AI Trading Agent is designed as a modular, decoupled quantitative research and trading system. It enforces strict separation between statistical computation, AI hypothesis generation, deterministic risk verification, and exchange execution.

```
+---------------------------------------------------------------------------------------------------+
|                                      DATA INGESTION & PIPELINE                                    |
|  +--------------------+   +-----------------------+   +--------------------+                      |
|  | Yahoo Finance GC=F |   | Delta Exchange REST/WS|   | Local Tick/Parquet |                      |
|  +---------+----------+   +-----------+-----------+   +---------+----------+                      |
|            |                          |                         |                                 |
|            +--------------------------+-------------------------+                                 |
|                                       v                                                           |
|                          +------------------------+                                               |
|                          | Raw Storage (data/raw) | (Immutable)                                   |
|                          +-----------+------------+                                               |
|                                       v                                                           |
|                          +------------------------+                                               |
|                          | Data Validation Engine | --> Quality Reports & Quarantined Data        |
|                          +-----------+------------+                                               |
|                                       v                                                           |
|                          +------------------------+                                               |
|                          |  Normalized Parquet    | (data/normalized, data/timeframe)             |
|                          +-----------+------------+                                               |
+---------------------------------------|-----------------------------------------------------------+
                                        v
+---------------------------------------------------------------------------------------------------+
|                                      QUANTITATIVE ANALYTICS & REGIME                              |
|  +--------------------+   +-----------------------+   +--------------------+                      |
|  | Technical Features |   | Multi-Timeframe Sync  |   | Market Regime      |                      |
|  | (ATR, RSI, BB,...) |   | (1M -> 5M -> 1H -> 4H)|   | (Trend, Range, Vol)|                      |
|  +--------------------+   +-----------------------+   +--------------------+                      |
+---------------------------------------|-----------------------------------------------------------+
                                        v
+---------------------------------------------------------------------------------------------------+
|                                  AI STRATEGY FACTORY (PHASE 2)                                    |
|  +--------------------+   +-----------------------+   +--------------------+                      |
|  | Market Analyst     |   | Strategy Researcher   |   | Performance Analyst|                      |
|  | (Structure/Regime) |   | (Hypothesis Generator)|   | (Failure Diagnosis)|                      |
|  +--------------------+   +-----------+-----------+   +--------------------+                      |
+---------------------------------------|-----------------------------------------------------------+
                                        v
+---------------------------------------------------------------------------------------------------+
|                                  DETERMINISTIC BACKTESTER & VALIDATOR                             |
|  +--------------------+   +-----------------------+   +--------------------+                      |
|  | Bar-by-Bar Matcher |   | Realism Model         |   | Validation Suite   |                      |
|  | (Vector / Event)   |   | (Slippage, Spread, Fee|   | (WFA, Monte Carlo, |                      |
|  +--------------------+   +-----------------------+   |  OOS Gate, Stress) |                      |
|                                                       +---------+----------+                      |
+-----------------------------------------------------------------|---------------------------------+
                                                                  v
+---------------------------------------------------------------------------------------------------+
|                                  DETERMINISTIC RISK & EXECUTION PIPELINE                          |
|                                                                                                   |
|     Strategy Signal                                                                               |
|            |                                                                                      |
|            v                                                                                      |
|   +-------------------+       REJECTED                                                            |
|   | Deterministic     | -------------------> [ Signal Dropped / Audit Log ]                       |
|   | Risk Engine       |                                                                           |
|   +--------+----------+                                                                           |
|            | APPROVED                                                                             |
|            v                                                                                      |
|   +-------------------+                                                                           |
|   | Kill Switch Gate  | ===> EMERGENCY HALT IF DRAWDOWN / LATENCY / SPREAD BREACHED               |
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
|   | Paper Trading Sim |                | Delta Adapter     | (LIVE_TRADING_ENABLED=false)         |
|   | & Trade Journal   |                | Exchange Client   |                                      |
|   +-------------------+                +-------------------+                                      |
+---------------------------------------------------------------------------------------------------+
```

---

## 2. MODULAR BOUNDARIES & PACKAGES

The repository follows clean layered architecture with explicit interfaces:

```
src/
├── core/                   # System configuration, logging, events, exceptions, base types
│   ├── config.py           # Pydantic BaseSettings (env vars, secrets, thresholds)
│   ├── logger.py           # Structured JSON / console logging with redaction
│   ├── events.py           # Event bus for internal messaging and audit log
│   └── constants.py        # Canonical symbols, timeframes, market sessions
│
├── data/                   # Data Ingestion, Cleaning, and Storage
│   ├── sources/            # Ingestion connectors (Yahoo, Delta REST/WS, CSV)
│   ├── validation/         # Quality checks (OHLC logic, gaps, spikes, timestamps)
│   ├── storage/            # Parquet file managers, metadata registry
│   └── resampler.py        # Multi-timeframe resampling (1m -> 5m, 15m, 1h, 4h, 1d)
│
├── quant/                  # Quantitative Analysis & Features
│   ├── indicators/         # Deterministic indicators (ATR, RSI, EMA, BB, MACD, etc.)
│   ├── regime/             # Market regime classifier (Trending, Ranging, High Vol, etc.)
│   └── sessions.py         # Gold market session identification (Asian, London, NY)
│
├── backtest/               # Deterministic Backtesting Engine
│   ├── engine.py           # Bar-by-bar matching engine with zero lookahead
│   ├── execution.py        # Fill models: spread, slippage, commission, funding
│   ├── metrics.py          # Complete quantitative performance metrics
│   ├── position.py         # Position tracking, margin, unrealized/realized P&L
│   └── types.py            # Backtest orders, fills, trade log definitions
│
├── validation/             # Overfitting & Robustness Engine (Phase 2)
│   ├── walk_forward.py     # Rolling window walk-forward analyzer
│   ├── monte_carlo.py      # Resampling, permutations, ruin probability
│   ├── out_of_sample.py    # Holdout partition testing gate
│   └── scoring.py          # Composite strategy robustness score
│
├── strategy/               # Strategy Definition and Repository
│   ├── base.py             # BaseStrategy abstract base class
│   ├── parameters.py       # Parameter schema, ranges, mutation rules
│   ├── registry.py         # Versioned strategy catalogue
│   └── strategies/         # Concrete strategy implementations
│
├── ai/                     # AI Strategy Factory & Agents (Phase 2)
│   ├── analyst.py          # Market structure and regime reasoning
│   ├── researcher.py       # Strategy hypothesis generator
│   ├── evaluator.py        # Performance & failure diagnosis agent
│   └── pine_transpiler.py  # Pine Script v5 generator
│
├── risk/                   # Deterministic Risk Management (Phase 3)
│   ├── engine.py           # Absolute risk rules (max daily loss, max exposure)
│   ├── sizing.py           # Position sizing (fixed fractional, volatility-adjusted)
│   └── kill_switch.py      # Emergency kill switch triggers & audit
│
├── execution/              # Execution & Exchange Adapters (Phase 3 & 4)
│   ├── base.py             # ExchangeAdapter abstract interface
│   ├── paper.py            # Real-time simulated paper trading engine
│   └── delta/              # Delta Exchange REST and WebSocket client
│
├── database/               # Relational Persistence
│   ├── connection.py       # Async SQLAlchemy session factory & engine
│   ├── models/             # SQLAlchemy ORM schemas
│   └── repository.py       # Clean repository pattern for queries
│
└── api/                    # FastAPI HTTP & WebSocket Interface
    ├── routes/             # REST endpoints (data, backtest, strategies, risk)
    ├── websockets/         # Real-time streaming for frontend dashboard
    └── schemas.py          # Pydantic request/response schemas
```

---

## 3. CORE SUB-SYSTEM SPECIFICATIONS

### 3.1 Data Pipeline
- **Raw Data Invariant:** Every downloaded payload is written to `data/raw/{source}/{instrument}/{timestamp}.json` or `.parquet` prior to any transformation.
- **Normalization Standard:** All data frames conform to:
  `[timestamp (UTC int64 ns), open (float64), high (float64), low (float64), close (float64), volume (float64), source (str), instrument (str)]`.
- **Validation Suite:** Detects:
  - Non-monotonic timestamps or duplicates
  - High < Low, High < Open, High < Close, Low > Open, Low > Close
  - Missing candle intervals (gaps based on market open/close calendar)
  - Price jumps $> 5\sigma$ relative to trailing 20-bar ATR

### 3.2 Backtesting Execution Model
- **Fill Semantics:**
  - Signals generated at bar $t$ close can only execute on bar $t$ close (with modeled adverse slip) or bar $t+1$ open.
  - Intra-bar Stop Loss and Take Profit evaluation assumes conservative path traversal: if both SL and TP price levels are within the bar range $[Low, High]$, the Stop Loss is assumed to hit first.
- **Cost Modeling:**
  - Slippage: Modelled as base tick slippage plus volatility-proportional slippage.
  - Spread: Half-spread subtracted on buy, half-spread added on sell.
  - Commission: Maker/taker fee schedules matching Delta Exchange ($0.02\%$ maker, $0.05\%$ taker).

### 3.3 Risk Engine Invariant
The Risk Engine evaluates 8 mandatory deterministic gates before any order reaches execution:
1. `DAILY_LOSS_LIMIT`: Daily realized + unrealized drawdown $< \text{MaxDailyLossPercent}$.
2. `PORTFOLIO_DRAWDOWN`: Peak-to-trough equity drawdown $< \text{MaxPortfolioDrawdownPercent}$.
3. `MAX_CONSECUTIVE_LOSSES`: Recent loss streak $< \text{MaxLossStreak}$.
4. `POSITION_SIZE_CAP`: New order size does not exceed max risk per trade ($1.0\%$ default).
5. `MAX_OPEN_POSITIONS`: Concurrent active positions $\le \text{MaxConcurrentPositions}$ (default 1 for Gold).
6. `MIN_RISK_REWARD`: Reward-to-Risk ratio $\ge 1.5$.
7. `SPREAD_FILTER`: Current bid-ask spread $\le 3\times$ median spread.
8. `KILL_SWITCH_STATUS`: Global kill switch state is `ARMED_NORMAL`.

---

## 4. PHASE ROLLOUT ROADMAP

```
Phase 1: Foundation + Data + Backtesting (Current Target)
  -> Repository, Constitution, Architecture, Docker
  -> Ingestion (Yahoo GC=F, Delta REST, Local)
  -> Validation & Parquet storage
  -> Technical Indicators & Regimes
  -> Deterministic Backtester & Performance Metrics Engine
  -> FastAPI endpoints & Pytest suite
  -> Phase 1 Completion Report

Phase 2: AI Strategy Factory + Validation
  -> Multi-Agent System (Market Analyst, Strategy Researcher, Performance Analyst)
  -> Walk-Forward & Monte Carlo Engine
  -> Overfitting & Robustness Scoring
  -> Pine Script v5 Generator

Phase 3: Paper Trading + Dashboard + Risk
  -> Real-Time Delta WebSocket & Paper Engine
  -> Deterministic Risk Engine & Kill Switches
  -> React 18 + Vite + Tailwind Dashboard
  -> Trade Journal & System Health

Phase 4: Delta Live Execution + Production
  -> Authenticated Delta Exchange API
  -> Order Reconciliation & Guardrails
  -> Production Hardening & VPS Deployment
```
