# TESTING STRATEGY & QUALITY ASSURANCE SPECIFICATION
*Test Hierarchy, Coverage Requirements, Fixtures, and Verification Gates*

---

## 1. TESTING TIERS

The testing pyramid spans five mandatory tiers:

1. **Unit Tests (`tests/unit/`):**
   - Pure function tests: Indicators (SMA, EMA, ATR, RSI, Bollinger Bands, MACD).
   - Metrics verification: Mathematical accuracy of Sharpe, Sortino, Max Drawdown, Profit Factor, Expectancy, and MAE/MFE against benchmark datasets.
   - Data validation logic: Detection of gaps, bad OHLC bars, monotonic timestamp violations.
   - Risk rules: Sizing clamp, drawdown breach, kill switch trigger.
2. **Integration Tests (`tests/integration/`):**
   - End-to-end data pipeline: Ingestion -> Validation -> Parquet write -> Parquet read.
   - Database repository tests: CRUD operations on strategy versions, backtest trades, metadata records.
   - Backtest engine runs: Running reference strategies on real/synthetic Gold data and verifying equity curve calculations.
3. **API Tests (`tests/api/`):**
   - FastAPI client tests verifying HTTP status codes, request validation, error responses, and payload formatting.
4. **Stress & Failure Injection Tests (`tests/stress/`):**
   - Corrupted data feeds, missing columns, sudden price drops ($> 20\%$), rapid consecutive losses.
   - Ensuring system halts cleanly rather than generating faulty orders.

---

## 2. COVERAGE STANDARDS & VERIFIED RESULTS
- Overall codebase coverage target: $\ge 85\%$.
- Verified Phase 1 Rework Coverage: **91%** (2075 / 2274 statements covered).
- Test Execution Status: **60 tests passed, 0 failed, 0 skipped**.
- Zero failing tests permitted before progressing to subsequent phases.

---

## 3. VERIFIED PHASE 1 TEST SUITE BREAKDOWN
- `tests/unit/test_dukascopy.py`: 5 tests — LZMA `.bi5` binary record unpacking, point-scaling (1000.0 for XAUUSD), checkpoint caching, tick-to-candle aggregation, UTC timestamp validation.
- `tests/unit/test_data_quality.py`: 5 tests — explicit quarantine of NaN, None, +inf, -inf, non-numeric strings across OHLCV, invalid geometry filtering, immutability.
- `tests/unit/test_metrics.py`: 7 tests — Gross Profit Factor vs Net Profit Factor, institutional full-sample Sortino ratio ($\sqrt{\frac{1}{N}\sum\min(0, R_i)^2}$), zero-downside capping, negative Sortino, empty series.
- `tests/unit/test_database.py`: 4 tests — SQLite `PRAGMA foreign_keys = ON` enforcement, child record orphan rejection, cascade delete, JSON parameter persistence, UTC datetimes, canonical instrument seeding.
- `tests/unit/test_delta_source.py`: 4 tests — product listing, Gold derivative filtering, candle parsing (dict and array payloads), empty candle response handling.
- `tests/unit/test_yahoo_source.py`: 3 tests — Yahoo Chart API parsing, empty results rejection, missing bar handling.
- `tests/unit/test_local_source.py`: 3 tests — CSV and Parquet loading, column standardization, file validation.
- `tests/unit/test_regime_and_sessions.py`: 4 tests — Market session tagging (Asian, London, London-NY overlap, NY, Off-hours), regime classification (Trending Up/Down, Abnormal, Ranging).
- `tests/unit/test_events.py`: 1 test — Domain event immutability, frozen dataclasses, UUID generation.
- `tests/unit/test_indicators.py`: 4 tests — EMA, ATR, RSI, Bollinger Bands mathematical accuracy.
- `tests/unit/test_resampler.py`: 2 tests — multi-timeframe aggregation from 1m bars, edge cases, frequency validation.
- `tests/unit/test_strategies.py`: 6 tests — Strategy registry catalogue and lookup, parameter bounds enforcement, EMA Crossover, ATR Breakout, RSI Mean Reversion.
- `tests/integration/test_backtest_engine.py`: 5 tests — Next-bar open execution model, zero lookahead bias verification, intra-bar Stop Loss tie-breaker, execution cost application.
- `tests/integration/test_parquet_storage.py`: 1 test — Partitioned Parquet read/write, schema validation, metadata persistence.
- `tests/api/test_api_endpoints.py`: 6 tests — Health check, system info, strategy listings, backtest execution & retrieval flow, data routes, API error handling (404, 400).

