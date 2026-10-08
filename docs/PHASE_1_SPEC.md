# PHASE 1 TECHNICAL SPECIFICATION & ACCEPTANCE CRITERIA
**System:** Gold AI Quantitative Trading System (`AGentTrXPAUS`)  
**Phase:** Phase 1 — Foundation, Data Ingestion, Quantitative Features & Deterministic Backtesting  
**Status:** Canonical Frozen Specification (Rework Baseline)  
**Governing Document:** `PROJECT_CONSTITUTION.md`

---

## 1. PURPOSE & BOUNDARIES

Phase 1 provides the foundational quantitative research infrastructure for the system. It strictly focuses on data ingestion, data hygiene, quantitative analytics, deterministic strategy backtesting, and relational persistence.

### What Phase 1 Includes:
1. Repository structure, environment configuration, and structured logging.
2. Primary historical data ingestion: **Dukascopy XAUUSD** (spot gold).
3. Secondary historical data ingestion: **Yahoo Finance GC=F** (continuous COMEX gold futures).
4. Public exchange metadata discovery & market data ingestion: **Delta Exchange** (`PAXGUSDT` / `XAUUSD_PERP`).
5. Automated Data Quality Engine auditing 9 corruption types with quarantine and quality scoring.
6. Atomic Parquet storage engine with SHA-256 integrity hashing and dataset registry.
7. Deterministic multi-timeframe resampler (1m base to 5m, 15m, 30m, 1h, 4h, 1d UTC bars).
8. Deterministic quantitative indicators, Gold market session tagging, and market regime detection.
9. Strategy framework (`BaseStrategy`, `StrategyRegistry`) and three reference strategies.
10. Deterministic backtesting engine with realistic costs, bar close / next open fills, and conservative SL/TP tie-breaking.
11. Institutional performance metrics suite (Sharpe, standard Sortino, Gross Profit Factor, Drawdown, Expectancy, MAE/MFE).
12. Database persistence (SQLite dev fallback, PostgreSQL production) and Alembic migrations.
13. FastAPI REST endpoints for data inspection, backtesting execution, and system health.
14. Unit and integration test suite with $\ge 85\%$ test coverage.

### What Phase 1 Explicitly Excludes:
- **Zero LLM / AI dependencies:** No LLM API calls, local model inferences, or AI agents (reserved for Phase 2).
- **Zero Redis dependencies:** No requirement for a running Redis server for Phase 1 tests or backend operation (reserved for Phase 3).
- **Zero Live Trading / Execution:** No authenticated order submission or exchange private keys (reserved for Phase 4).
- **Zero Web Frontend:** No React application is built in Phase 1 (reserved for Phase 3).

---

## 2. MARKET & INSTRUMENT DEFINITIONS

All market data, indicators, backtests, and stored entities must strictly distinguish between instrument domains:

```text
+---------------------------------------------------------------------------------------------------+
| INSTRUMENT SPECIFICATION MATRIX                                                                  |
+-------------------+-------------+--------------------+------------+---------------+---------------+
| Symbol            | Domain      | Primary Venue      | Currency   | Multiplier    | Role          |
+-------------------+-------------+--------------------+------------+---------------+---------------+
| XAUUSD            | Spot        | Dukascopy          | USD        | 1.0 oz        | Primary Quant |
| GC=F              | Futures     | COMEX (Yahoo)      | USD        | 100.0 oz      | Macro / Comp  |
| PAXGUSDT          | Derivative  | Delta Exchange     | USDT       | 0.001 oz      | Execution Ref |
| XAUUSD_PERP       | Derivative  | Delta Exchange     | USDT       | Per Contract  | Execution Ref |
+-------------------+-------------+--------------------+------------+---------------+---------------+
```

### Invariant:
Under no circumstances may COMEX futures (`GC=F`) or Delta derivatives silently replace or overwrite Dukascopy `XAUUSD` as the primary research baseline. Every dataset and backtest run must record its specific `(source, instrument, timeframe)` identifier.

---

## 3. HISTORICAL DATA INGESTION & QUALITY SPECIFICATIONS

### 3.1 Data Ingestion Connectors
1. **Dukascopy Connector (`src/data/sources/dukascopy.py`):**
   - Ingests spot `XAUUSD` historical tick or 1-minute candle archives.
   - Formats timestamps to UTC.
   - Writes raw archives immutably to `data/raw/dukascopy/XAUUSD/`.
2. **Yahoo Finance Connector (`src/data/sources/yahoo.py`):**
   - Ingests continuous COMEX Gold futures (`GC=F`).
   - Downloads 1m, 5m, 15m, 30m, 1h, 1d historical bars.
   - Writes raw archives immutably to `data/raw/yahoo/GC=F/`.
3. **Delta Exchange Public Connector (`src/data/sources/delta_client.py`):**
   - Dynamically discovers Gold products via Delta REST API (`https://api.delta.exchange/v2/products`).
   - Ingests public historical candles for active Gold products (e.g. `PAXGUSDT`).
   - Records full contract metadata (tick size, contract size, fees, funding interval).
   - Writes raw archives to `data/raw/delta/{symbol}/`.
4. **Local File Connector (`src/data/sources/local.py`):**
   - Supports importing user-provided CSV or Parquet files with explicit source and symbol tagging.

### 3.2 Canonical Data Schema
Normalized dataframes must conform to:
| Column | Type | Invariant |
| :--- | :--- | :--- |
| `timestamp` | `datetime64[ns, UTC]` | Strictly monotonic, unique, UTC timezone |
| `open` | `float64` | $> 0$, non-null, finite |
| `high` | `float64` | $\ge \max(open, low, close)$, non-null |
| `low` | `float64` | $\le \min(open, high, close)$, non-null |
| `close` | `float64` | $> 0$, non-null, finite |
| `volume` | `float64` | $\ge 0$, non-null, finite |
| `source` | `string` | Data source identifier (e.g., `dukascopy`, `yahoo`, `delta`) |
| `instrument`| `string` | Instrument symbol (e.g., `XAUUSD`, `GC=F`, `PAXGUSDT`) |

### 3.3 Data Quality Engine Rules
The quality engine must audit every ingested batch against 9 distinct corruption categories:
1. **NaN / Null Elimination:** Any bar containing `NaN`, `None`, or `inf` in `open`, `high`, `low`, `close`, or `volume` is quarantined and excluded from `clean_df`.
2. **Monotonic Order:** Timestamps must be sorted in strictly ascending chronological order.
3. **Duplicate Detection:** Duplicate timestamps are identified and removed (preserving the latest record or first valid).
4. **OHLC Geometry:** Verifies $High \ge Low$, $High \ge Open$, $High \ge Close$, $Low \le Open$, $Low \le Close$.
5. **Zero / Negative Prices:** Bars where $Open, High, Low, Close \le 0$ are rejected.
6. **Negative Volume:** Volumes $< 0$ are clamped to zero or quarantined.
7. **Price Spike Filter:** Bars where $|Close_t - Close_{t-1}| > 5 \times ATR_{20}$ are flagged for investigation.
8. **Session Gap Detection:** Gaps between successive bars are compared against market session calendars (Sunday open to Friday close).
9. **Quality Scoring & Reporting:** Computes composite quality score ($0.0 - 100.0$) and produces both JSON and Markdown audit reports.

### 3.4 Multi-Timeframe Resampling
- Base resolution: 1m bars (when available).
- Target resolutions: 5m, 15m, 30m, 1h, 4h, 1d.
- Boundary convention: Left-closed, left-labeled intervals ($[t, t + \Delta t)$ labeled with timestamp $t$).
- Resampling rules:
  - $Open = Open_{\text{first}}$
  - $High = \max(High)$
  - $Low = \min(Low)$
  - $Close = Close_{\text{last}}$
  - $Volume = \sum(Volume)$
- Zero lookahead: Intermediate unfinished bars must not be included.

---

## 4. QUANTITATIVE ANALYTICS & REGIME CLASSIFICATION

### 4.1 Technical Indicators
All indicators must be implemented as deterministic functions with zero external lookahead:
- Simple Moving Average (SMA)
- Exponential Moving Average (EMA)
- Weighted Moving Average (WMA)
- Average True Range (ATR)
- Bollinger Bands (Upper, Middle, Lower, Bandwidth, %B)
- Relative Strength Index (RSI - Wilder's smoothing)
- Moving Average Convergence Divergence (MACD, Signal, Histogram)
- Average Directional Index (ADX, +DI, -DI)
- Donchian Channels (Upper, Lower, Middle)

### 4.2 Gold Market Sessions (UTC)
- **Asian Session:** 00:00 – 08:00 UTC (Tokyo, Hong Kong, Singapore)
- **London Session:** 08:00 – 16:00 UTC (London institutional fixing at 10:30 and 15:00 UTC)
- **New York Session:** 13:00 – 21:00 UTC (COMEX futures trading, US macro data releases)
- **London / New York Overlap:** 13:00 – 16:00 UTC (Peak daily liquidity and volatility)

### 4.3 Deterministic Market Regime Classifier
Assigns each candle one primary regime tag:
- `TRENDING_UP`: ADX $> 25$, EMA(20) $>$ EMA(50), Close $>$ EMA(20).
- `TRENDING_DOWN`: ADX $> 25$, EMA(20) $<$ EMA(50), Close $<$ EMA(20).
- `RANGING`: ADX $\le 20$, price oscilating between Bollinger Bands.
- `BREAKOUT`: Donchian channel penetration with expanding volume.
- `REVERSAL`: RSI divergence or exhaustion outside Bollinger Bands.
- `HIGH_VOLATILITY`: ATR $> 1.5 \times \text{SMA}(ATR, 50)$.
- `LOW_VOLATILITY`: ATR $< 0.7 \times \text{SMA}(ATR, 50)$.
- `ABNORMAL`: Extreme price jump $> 5\sigma$ or flash crash profile.

---

## 5. DETERMINISTIC BACKTESTING ENGINE & METRICS

### 5.1 Execution Model
1. **Zero Lookahead:** At bar $t$, only data up to and including bar $t$ is accessible to strategy signal generation.
2. **Fill Modes:**
   - **Bar Close Fill:** Signal evaluated at bar $t$ close fills at $Close_t \pm \text{HalfSpread} \pm \text{Slippage}$.
   - **Next Bar Open Fill:** Signal evaluated at bar $t$ close fills at $Open_{t+1} \pm \text{HalfSpread} \pm \text{Slippage}$.
   - Both fill models must be supported via `BacktestConfig.execution_on_next_bar_open`.
3. **Conservative Intra-Bar SL/TP Tie-Breaker:**
   If a bar's price range $[Low_t, High_t]$ encompasses both the position's Stop Loss and Take Profit levels:
   $$\text{Stop Loss is assumed to hit first (worst-case outcome)}$$
4. **Cost Simulation:**
   - **Spread:** Configurable in ticks/pips or USD (e.g., $0.20$ USD for spot gold).
   - **Slippage:** Configurable base tick slippage plus volatility adjustment ($0.05$ USD base).
   - **Fees:** Explicit maker fee ($0.02\%$) and taker fee ($0.05\%$).
   - **Funding Cost:** Perpetual contracts accrue 8-hour funding rates if holding through funding timestamps.

### 5.2 Performance Metrics Standards
1. **Total Return & CAGR:** Geometric compounded return and annualized equivalent.
2. **Sharpe Ratio:**
   $$\text{Sharpe} = \frac{\bar{R} - R_f}{\sigma_R} \times \sqrt{252 \times \text{bars\_per\_day}}$$
3. **Institutional Sortino Ratio:**
   Downside semi-deviation $\sigma_d$ must be calculated over the entire distribution of $N$ returns with non-negative returns zeroed out:
   $$\sigma_d = \sqrt{\frac{1}{N} \sum_{i=1}^{N} \min(0, R_i - \tau)^2}$$
   $$\text{Sortino} = \frac{\bar{R} - \tau}{\sigma_d} \times \sqrt{252 \times \text{bars\_per\_day}}$$
   *(Filter-only approaches that compute standard deviation over only the subset of losing bars introduce severe sample-size distortion and are strictly forbidden).*
4. **Gross Profit Factor:**
   $$\text{Profit Factor} = \frac{\sum \text{Gross Gains}}{\sum |\text{Gross Losses}|}$$
5. **Drawdown Metrics:**
   - Max Drawdown Percent ($\%$).
   - Max Drawdown Absolute ($\$$).
   - Max Drawdown Duration (consecutive bars under peak equity).
   - Recovery Factor: $\frac{\text{Net Profit}}{\text{Max Drawdown (\$)}} $.
6. **Expectancy:**
   $$\text{Expectancy (\$) } = (\text{Win Rate} \times \text{Avg Win}) - (\text{Loss Rate} \times \text{Avg Loss})$$
   $$\text{Expectancy (R)} = (\text{Win Rate} \times \text{Avg Win R}) - (\text{Loss Rate} \times 1.0)$$
7. **Trade Profiling:**
   - Maximum Adverse Excursion (MAE) and Maximum Favorable Excursion (MFE) per trade.
   - Long vs. Short performance breakdown.
   - Total commissions and slippage paid.

---

## 6. RELATIONAL PERSISTENCE & REST API

### 6.1 Database Engine
- **Local Dev:** SQLite via `sqlite+aiosqlite` at `data/gold_trading.db`.
- **Production:** PostgreSQL 16 via `postgresql+asyncpg`.
- **Migrations:** Alembic version-controlled migrations.

### 6.2 REST Endpoints
- `GET /health` — Database, storage, and system health status.
- `GET /system/info` — Active phase, canonical instruments, system version.
- `POST /data/ingest` — Trigger dataset ingestion with quality audit.
- `GET /data/status` — Ingested datasets, bar counts, date ranges, and quality scores.
- `GET /data/quality-reports` — List and inspect data validation audit reports.
- `GET /data/candles` — Retrieve normalized OHLCV bars with regime annotations.
- `GET /strategies` — List registered strategies and parameter specifications.
- `GET /strategies/{strategy_id}` — Detailed strategy parameter schemas.
- `POST /backtest/run` — Run backtest, compute metrics, and persist trade log.
- `GET /backtest/{backtest_id}` — Fetch completed backtest run results and trade history.

---

## 7. ACCEPTANCE CRITERIA & VERIFICATION GATES

Phase 1 cannot be approved or marked complete until all 10 criteria pass:

| # | Gate Requirement | Verification Method |
| :--- | :--- | :--- |
| **G1** | **Dukascopy Primary Dataset** | `src/data/sources/dukascopy.py` implemented and verified downloading `XAUUSD` bars. |
| **G2** | **Instrument Separation** | Research (`XAUUSD`, `GC=F`) and Execution (`PAXGUSDT`) are distinct entities in DB and Parquet. |
| **G3** | **Data Quality Invariants** | Quality engine rejects/drops NaNs, non-positive OHLC, bad geometry; generates valid quality report. |
| **G4** | **Deterministic Resampling** | 1m to 5m, 15m, 30m, 1h, 4h, 1d resampling produces correct UTC bars with zero lookahead. |
| **G5** | **Indicator & Regime Correctness** | All 9 indicators and Gold market sessions / regimes produce deterministic, verified values. |
| **G6** | **Zero Lookahead Backtester** | Backtester supports bar close and next bar open fills; conservative SL/TP tie-breaker passes tests. |
| **G7** | **Standard Metrics Formulation** | Sortino uses full-sample downside semi-deviation; Profit Factor uses Gross values; MaxDD is exact. |
| **G8** | **Zero Redis Dependency** | `docker-compose.yml` does not gate backend on Redis; backend and tests run without Redis. |
| **G9** | **Zero LLM Dependency** | Entire Phase 1 pipeline executes with zero external LLM API calls or AI dependencies. |
| **G10**| **Test Coverage $\ge 85\%$** | Pytest passes all unit and integration tests with line coverage $\ge 85\%$ across all Phase 1 files. |

