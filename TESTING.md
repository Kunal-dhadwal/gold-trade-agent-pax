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

## 2. COVERAGE STANDARDS

- Overall codebase coverage target: $\ge 85\%$.
- Core safety modules (`src/risk/`, `src/backtest/`, `src/data/validation/`): $\ge 95\%$ test coverage.
- Zero failing tests permitted before progressing to subsequent phases.
