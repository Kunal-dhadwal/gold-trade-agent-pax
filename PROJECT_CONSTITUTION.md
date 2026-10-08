# GOLD AI TRADING AGENT — PROJECT CONSTITUTION
*Authoritative Source of Truth, Core Invariants, System Boundaries, and Engineering Governance*

---

## 1. PREAMBLE & SOURCE OF TRUTH PRIORITY

This Constitution is the highest-level governing authority for the Gold AI Trading Agent codebase. No architectural decision, implementation choice, refactoring, or chat suggestion may violate the tenets established in this document.

### The Canonical Governance Hierarchy:
```text
PROJECT_CONSTITUTION.md (Supreme Authority)
        ↓
ARCHITECTURE.md
        ↓
PHASE SPECIFICATIONS (e.g., docs/PHASE_1_SPEC.md)
        ↓
ARCHITECTURAL DECISION RECORDS (docs/ADR/)
        ↓
Implementation Code
        ↓
Verification & Audit Reports
```

### The Conflict Invariant (Source of Truth Rule):
Future implementation decisions **MUST** adhere strictly to the following priority:
1. `PROJECT_CONSTITUTION.md`
2. Current Phase Specification (`docs/PHASE_X_SPEC.md`)
3. Approved Architectural Decision Records (`docs/ADR/`)
4. System Architecture Documentation (`ARCHITECTURE.md`)
5. Existing Codebase Implementation
6. Chat Instructions / User Context

> **CRITICAL RULE:** If an implementation detail, instruction, or tool output conflicts with this Constitution, developers and AI agents must **STOP** immediately and report the discrepancy. Never silently choose an unapproved architecture or workaround.

---

## 2. PRODUCT PURPOSE & OPTIMIZATION HIERARCHY

The **Gold AI Trading Agent** is an institutional-grade, multi-agent quantitative research and execution platform focused exclusively on Gold.

### System Optimization Priority:
$$\mathbf{Correctness} > \mathbf{Data\ Integrity} > \mathbf{Reproducibility} > \mathbf{Risk\ Control} > \mathbf{Testability} > \mathbf{Maintainability} > \mathbf{Performance} > \mathbf{Development\ Speed}$$

### Core Trading Truth:
> **Profitability is NEVER guaranteed.**
> The system optimizes for discovering, testing, and managing strategies with robust positive expectancy, controlled drawdown, statistical repeatability, and verifiable out-of-sample survival. The system rejects any curve-fitting, lookahead bias, or marketing claims of guaranteed returns.

---

## 3. MARKET & INSTRUMENT SPECIFICATION

### Primary Market:
**Gold Only.**

### Separation of Instrument Domains:
The architecture must explicitly distinguish between **Research Gold Instruments** and **Execution Gold Instruments**. Under no circumstances may different instruments be silently treated as identical:

1. **Research Gold Instruments (Spot & Futures):**
   - **Spot Gold (`XAUUSD`):** Price of 1 troy ounce of gold in USD. Tight continuous spreads, institutional liquidity.
   - **COMEX Gold Futures (`GC=F`):** Standard continuous futures contract (100 troy ounces per contract). Used for macro trend analysis and multi-decade backtest validation.
2. **Execution Gold Instruments (Delta Exchange Derivatives):**
   - **Delta Perpetual / Futures (`PAXGUSDT` / `XAUUSD_PERP`):** Derivative instruments traded on Delta Exchange. Quote currency, settlement asset (USDT), margin requirement, tick size, funding rates, and contract multipliers (e.g. 0.001 oz for PAXG) are distinct from spot gold.

*Invariant:* All database models, Parquet storage paths, indicators, and backtest configurations must explicitly tag and preserve the instrument identifier (`instrument_id`) and dataset provenance.

---

## 4. HISTORICAL RESEARCH DATA POLICY

### Primary Free Research Dataset:
**Dukascopy XAUUSD** is the primary free historical research dataset for the platform.

### Secondary Datasets:
- COMEX Gold Continuous Futures (`GC=F` via Yahoo Finance)
- Delta Exchange historical market data (`PAXGUSDT` / `XAUUSD`)
- Local Parquet / CSV tick archives

### Provenance Invariant:
- `GC=F` or any other source must **NOT** silently replace Dukascopy `XAUUSD` as the primary research dataset.
- Every dataset partition must preserve complete provenance: Source, Instrument, Timezone (UTC), Resolution, Timestamp Range, Bar Count, Checksum (SHA-256), and Quality Audit Score.

---

## 5. DELTA EXCHANGE INTEGRATION POLICY

1. **Execution Venue Role:** Delta Exchange is the target execution venue for paper and live derivative operations.
2. **Dynamic Product Discovery:** The exact Gold execution instrument must **NEVER** be hardcoded based on assumptions. It must be dynamically discovered, queried, and verified against Delta Exchange's public product metadata API (`/v2/products`).
3. **Preserved Product Specifications:**
   The platform must strictly parse and record:
   - Product ID, Symbol, Contract Type (`perpetual_futures`, `futures`)
   - Contract Value / Multiplier (e.g., 0.001 for PAXG)
   - Tick Size, Minimum Order Size, Maximum Order Size
   - Quoting Asset (e.g., USDT) and Settling Asset (e.g., USDT)
   - Trading Status (`operational`, `halted`)
   - Maker Fee Rate (0.02%) and Taker Fee Rate (0.02% / 0.05%)
   - Funding Mechanism (8-hour mark price TWAP clamp)
4. **Liquidity Observation Principle:**
   The observed low traded volume on Delta Exchange's `PAXGUSDT` is documented as an **operational empirical observation**. It must not be silently treated as proof that the instrument cannot be integrated. Rather, the system must support synthetic index benchmarking (`.DEPAXGUSDT`) and warn the operator of liquidity/slippage constraints.

---

## 6. TIME & TIMEFRAME SPECIFICATIONS

1. **Strict UTC Standard:** All internal timestamps, candle open times, execution records, logs, and database records **MUST** use UTC. Local timezone conversions are strictly limited to UI presentation.
2. **Required Primary Research Timeframes:**
   $$\mathbf{5m},\ \mathbf{15m},\ \mathbf{30m},\ \mathbf{1h},\ \mathbf{4h},\ \mathbf{1d}$$
3. **1-Minute (1m) Role:**
   1m data serves as the canonical base resolution for multi-timeframe resampling. It is optional and must **not** become a mandatory blocking dependency if historical archives provide higher timeframe bars directly.
4. **Resampling Integrity:**
   Multi-timeframe resampling must be purely deterministic with zero lookahead bias. Standard candle open times are labeled using left-closed, left-labeled intervals (`[t, t + \Delta t)` labeled as $t$).

---

## 7. SEQUENTIAL 4-PHASE ARCHITECTURE BOUNDARIES

The system development is strictly partitioned into 4 sequential phases. No phase may be started until the preceding phase is formally verified, audited, and approved.

### Phase 1: Foundation + Data Ingestion + Deterministic Backtester
- Repository structure, Git workflow, configuration, safe logging.
- Relational schema (PostgreSQL with SQLite local dev fallback) and Alembic migrations.
- Historical data ingestion (Dukascopy primary, Yahoo secondary, Delta public, Local files).
- Automated Data Quality Engine with comprehensive checks (NaN drop, geometry, duplicates, monotonic order, spikes, gaps).
- Parquet storage engine with PyArrow atomic writes and SHA-256 metadata registry.
- Multi-timeframe resampling engine (1m $\rightarrow$ 5m, 15m, 30m, 1h, 4h, 1d).
- Deterministic technical indicators (SMA, EMA, WMA, ATR, BB, RSI, MACD, ADX, Donchian).
- Gold market session tagging (Asian, London, NY, Overlap).
- Deterministic market regime classifier.
- Strategy base interface, parameter schemas, and reference strategies.
- Deterministic backtesting engine with realistic costs (spread, slippage, fees, position sizing, conservative intra-bar SL/TP tie-breaking, next-bar open & close fill models).
- Quantitative metrics suite (Sharpe, standard Sortino, MaxDD %, MaxDD USD, Recovery Factor, Profit Factor, Expectancy, Average R, MAE, MFE).
- REST API endpoints for data, strategies, backtesting, and system health.
- $\ge 85\%$ test coverage and comprehensive documentation.

### Phase 2: AI Strategy Factory + Validation Suite
- Multi-agent AI architecture (Market Analyst, Strategy Researcher, Performance Analyst).
- LLM Provider Abstraction (Ollama/local primary, Gemini secondary).
- Autonomous Strategy Factory: hypothesis generation, formalization, candidate ranking.
- Walk-Forward Analysis (WFA) with rolling in-sample/validation windows.
- Monte Carlo simulations (trade reshuffling, equity curve permutations, ruin probability).
- Out-of-Sample (OOS) holdout gate.
- Overfitting detection and composite robustness scoring.
- TradingView Pine Script v5 transpiler with strict execution parity verification.

### Phase 3: Paper Trading + Dashboard + Risk Engine
- Delta Exchange live market data WebSocket feed.
- Real-time paper trading engine with virtual order books.
- Comprehensive trade journal.
- Deterministic Risk Engine with 8 absolute gates (drawdown, max daily loss, exposure, SL enforcement).
- Emergency Kill Switch circuit breaker.
- Real-time state cache and pub/sub channels via **Redis 7**.
- Institutional React 18 + TypeScript + Vite trading dashboard.
- Live alerts and telemetry.

### Phase 4: Delta Live Execution + Production Hardening
- Authenticated Delta Exchange integration (HMAC SHA-256 signature, REST order router).
- Real-time order reconciliation daemon.
- Position reconciliation and bracket stop-loss verification.
- Production VPS deployment and systemd / Docker container orchestration.
- Live trading activation gates (paper trading certification, explicit operator sign-off).

---

## 8. INFRASTRUCTURE & DEPENDENCY INVARIANTS

### Tenet: Redis is Phase 3
- **FROZEN DECISION:** Redis is an infrastructure component reserved strictly for **Phase 3** (real-time market caching, paper trading pub/sub, dashboard state).
- **Phase 1 Invariant:** Redis is **NOT** a Phase 1 runtime dependency. Phase 1 must execute completely, pass all tests, and start the FastAPI service with zero requirement for a running Redis server. In `docker-compose.yml`, the Phase 1 backend service must not be blocked by Redis health conditions.

### Tenet: Frontend is Phase 3
- The React + TypeScript + Vite frontend dashboard belongs to Phase 3. No production web frontend is required or expected in Phase 1 or Phase 2.

### Tenet: LLM Isolation & Provider Abstraction
- **Phase 1:** Zero LLM dependencies. All Phase 1 modules (indicators, backtest, regimes, metrics) are 100% deterministic Python/NumPy code.
- **Phase 2:** LLM integration operates strictly behind an abstract provider interface (`LLMProvider`). The preferred default provider is local/self-hosted (e.g. Ollama); secondary optional provider is Gemini free tier.
- **Veto Authority:** Under no circumstances may an LLM directly communicate with an exchange socket, issue live orders, or override deterministic risk rules.

---

## 9. QUANTITATIVE RIGOR & BACKTESTING RULES

### 1. Zero Lookahead Bias
No calculation, feature, or signal at bar $t$ may use data from bar $t+1$ or beyond.

### 2. Execution Timing & Realism
- Signals generated at the close of bar $t$ can execute at bar $t$ close (with modeled adverse slippage and half-spread) or at bar $t+1$ open. Both fill models must be supported.
- Spread, adverse slippage, and exchange fees must always be deducted.

### 3. Conservative Intra-Bar Path Traversal
If a bar's price range $[Low_t, High_t]$ encompasses both the active position's Stop Loss and Take Profit levels, and sub-bar tick data is unavailable, the simulation engine **MUST** conservatively assume that the **Stop Loss triggered first**.

### 4. Data Partitioning & Out-of-Sample Sanctity
Default chronological split:
- **60% In-Sample (Training / Exploration)**
- **20% Validation (Parameter Tuning)**
- **20% Out-of-Sample (Strict Holdout Gate)**
The Out-of-Sample partition must remain locked. It is forbidden to use OOS data during strategy generation, indicator selection, or parameter optimization.

### 5. Pine Script Parity Standard (Phase 2)
Generated TradingView Pine Script v5 code must achieve strict statistical parity with the Python backtest:
- $|\text{Trade Count}_{\text{Pine}} - \text{Trade Count}_{\text{Python}}| \le 5\%$
- $|\text{Net PnL}_{\text{Pine}} - \text{Net PnL}_{\text{Python}}| \le 5\%$
If parity fails, the strategy status is flagged as `PINE_PARITY_FAILED`.

---

## 10. RISK MANAGEMENT & OPERATIONAL SAFETY

### 1. Deterministic Risk Precedence
The Risk Engine holds unconditional veto power over all strategy signals, AI proposals, and operator inputs:
$$\text{Signal} \longrightarrow \mathbf{Deterministic\ Risk\ Engine} \longrightarrow \mathbf{Execution\ Engine}$$

### 2. Strategy Lifecycle State Machine
Every strategy must progress through the canonical state machine:
```text
[RESEARCH]
    ↓ (In-Sample Backtest Passes)
[BACKTESTED]
    ↓ (Walk-Forward, Monte Carlo, Stress Tests Pass)
[VALIDATED]
    ↓ (Out-of-Sample Holdout Gate Passes)
[OOS_PASSED]
    ↓ (Activated on Live Feed)
[PAPER_TRADING]
    ↓ (4 Weeks Paper Trial + >= 50 Trades + Execution Parity)
[LIVE_CANDIDATE]
    ↓ (Operator Sign-off + LIVE_TRADING_ENABLED=true)
[ACTIVE]

Failure at any point: -> [REJECTED]
Degradation in live/paper: -> [RETIRED]
```

### 3. Paper-to-Live Gate
A strategy may transition from `PAPER_TRADING` to `LIVE_CANDIDATE` only if it completes:
- Minimum **4 consecutive weeks** of real-time paper trading.
- Minimum **50 completed trades**.
- Slippage and fee realization matching backtest models within $15\%$.
- No strategy may become a live trading candidate based on backtest profitability alone.

### 4. Live Trading Hard-Lock
- Default setting: `LIVE_TRADING_ENABLED=false` must remain hardcoded in configuration templates.
- Phase 1, Phase 2, and Phase 3 must not contain authenticated live order placement capabilities.
- Zero API secrets, private keys, or passwords may be committed to Git, emitted in logs, or exposed to LLM prompts.

---

## 11. DATABASE SCHEMA & PHASE OWNERSHIP

1. Database migrations and SQLAlchemy models should reflect phase ownership.
2. Tables belonging to future phases (such as `paper_trades`, `live_trades`, `validation_results`, `agent_decisions`) must be documented explicitly if provisioned as schema foundations.
3. The existence of a schema table must **NEVER** be mistaken for, or documented as, functional application capability.

---

*Adopted as the Frozen Canonical Source of Truth for AGentTrXPAUS.*
