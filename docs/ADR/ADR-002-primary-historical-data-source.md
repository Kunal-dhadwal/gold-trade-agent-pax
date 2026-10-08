# ADR-002: Primary Free Historical Research Data Source
**Status:** Approved & Frozen  
**Date:** 2026-10-08  
**Author:** Lead Quantitative Systems Engineer  
**Governing Documents:** `PROJECT_CONSTITUTION.md`, `ARCHITECTURE.md`, `docs/PHASE_1_SPEC.md`

---

## 1. CONTEXT & PROBLEM STATEMENT

A core mandate of this project is to collect and manage free/public historical Gold data for quantitative research, market regime detection, and deterministic backtesting.

In the initial implementation of Phase 1, Yahoo Finance (`GC=F`) was treated as the default and primary data source in configuration, API query parameters, and test mocks. However:
1. `GC=F` represents COMEX continuous futures, which are subject to contract expiration rolls, roll gaps, and exchange trading halts.
2. Yahoo Finance imposes strict rate limits and offers limited historical depth for granular intraday timeframes (e.g. 1m data is typically capped at 7 days).
3. The agreed architectural requirement explicitly established **Dukascopy XAUUSD** as the primary free historical research dataset. Dukascopy provides multi-year, continuous, institutional-grade tick and 1-minute historical data for spot gold (`XAUUSD`) without roll distortions.

Allowing Yahoo `GC=F` to silently masquerade as the primary research source violated the project charter and compromised research fidelity.

---

## 2. DECISION

1. **Dukascopy XAUUSD as Primary Research Dataset:**
   - **Dukascopy XAUUSD** is frozen as the platform's primary free historical research dataset.
   - A dedicated ingestion connector (`src/data/sources/dukascopy.py`) must be implemented to fetch, parse, and validate Dukascopy historical data (tick or 1-minute resolution).
2. **Yahoo Finance GC=F as Secondary Research Dataset:**
   - Yahoo Finance continuous COMEX Gold futures (`GC=F`) is retained strictly as a secondary dataset for cross-market validation, macro trend confirmation, and futures comparison.
3. **Delta Exchange Market Data as Execution Venue Data:**
   - Public market data from Delta Exchange (`PAXGUSDT` / `XAUUSD`) is ingested for execution instrument characterization and paper trading benchmarking.
4. **Configuration Defaults:**
   - All default system settings, CLI tools, and REST API ingestion endpoints must prioritize `XAUUSD` from `dukascopy` as the primary instrument and source.

---

## 3. CONSEQUENCES

### Positive:
- **Uncompromised Data Quality:** Researchers have access to clean, granular, continuous spot gold prices with realistic institutional spreads.
- **Multi-Year Intraday Depth:** Allows rigorous walk-forward and out-of-sample backtesting across multiple economic cycles.
- **Standardized Baseline:** All baseline strategies and benchmark indicators are computed on a single canonical dataset.

### Negative / Trade-offs:
- Ingestion logic must handle Dukascopy data formats (bi5 / tick / minute binary or CSV structures).
- Requires robust local caching and Parquet normalization to minimize network bandwidth.

---

## 4. COMPLIANCE & VERIFICATION

1. `src/data/sources/dukascopy.py` must exist and be fully covered by unit tests.
2. Default configuration parameters (`DEFAULT_SOURCE`, `DEFAULT_INSTRUMENT`) must be set to `dukascopy` and `XAUUSD`.
3. In `data/raw/` and `data/normalized/`, the directory structure must place Dukascopy `XAUUSD` data at `data/raw/dukascopy/XAUUSD/` and `data/normalized/dukascopy/XAUUSD/`.
4. Automated tests must verify the parsing and validation of Dukascopy data feeds.

