# ADR-001: Separation of Research vs. Execution Instrument Domains
**Status:** Approved & Frozen  
**Date:** 2026-10-08  
**Author:** Lead Quantitative Systems Engineer  
**Governing Documents:** `PROJECT_CONSTITUTION.md`, `ARCHITECTURE.md`

---

## 1. CONTEXT & PROBLEM STATEMENT

The system focuses exclusively on Gold. However, "Gold" exists in multiple distinct commercial and financial forms across global markets:
1. **Spot Gold (`XAUUSD`):** Quoted in USD per 1 troy ounce. Highly liquid, continuous OTC interbank pricing, tight bid-ask spreads, zero expiration.
2. **COMEX Gold Futures (`GC=F`):** Standard exchange-traded futures contract (100 troy ounces per contract). Quoted in USD, subject to contract roll cycles, settlement rules, and distinct exchange trading hours.
3. **Crypto-Collateralized Derivatives (Delta Exchange `PAXGUSDT` / `XAUUSD_PERP`):** Derivative instruments traded on Delta Exchange. Quoted and settled in USDT. They have unique contract multipliers (e.g. 0.001 oz for PAXG), tick sizes, taker fees (0.05%), maker fees (0.02%), 8-hour funding rate mechanisms, and significantly lower observed order book depth than institutional spot gold.

In previous revisions, there was a risk of conflating these instruments or silently treating `GC=F` or `PAXGUSDT` as synonymous with `XAUUSD`. Doing so would corrupt quantitative research, produce unrealistic backtest fills, and distort slippage models.

---

## 2. DECISION

We formally decouple and freeze the architecture into two distinct instrument domains:

### A. Research Gold Domain
- **Primary Research Instrument:** Spot Gold (`XAUUSD`), sourced from Dukascopy.
  - Represents the purest baseline for technical, statistical, and quantitative hypothesis testing.
- **Secondary Research Instrument:** Continuous COMEX Gold Futures (`GC=F`), sourced from Yahoo Finance.
  - Used for cross-market validation, macro trend confirmation, and multi-decade structural analysis.

### B. Execution Gold Domain
- **Target Execution Instruments:** Delta Exchange Gold Contracts (`PAXGUSDT` and any additional Gold perpetual contracts discovered via Delta's product metadata API).
  - Used for live market data subscription (Phase 3), paper trading (Phase 3), and live execution (Phase 4).
  - Must never be hardcoded based on assumptions; metadata must be queried dynamically from `/v2/products`.

### Invariants:
1. **No Silent Substitution:** A strategy developed or backtested on `XAUUSD` must never be claimed to have been evaluated on `PAXGUSDT` or `GC=F`, and vice versa.
2. **Explicit Provenance Tuple:** All data storage paths, Parquet partitions, database tables, and backtest records must strictly persist the tuple:
   $$\mathbf{(source,\ instrument,\ timeframe)}$$
3. **Operational Liquidity Observation:** Traded volume on Delta's `PAXGUSDT` is low. This must be recorded as an operational finding. Synthetic index tracking (e.g. `.DEPAXGUSDT`) and realistic slippage penalties must be applied when modeling Delta execution.

---

## 3. CONSEQUENCES

### Positive:
- **Scientific Rigor:** Research backtests reflect true spot gold liquidity, while execution models accurately reflect Delta Exchange mechanics.
- **Cost Realism:** Commission, funding rate, multiplier, and tick size are matched precisely to the instrument being traded.
- **Traceability:** Auditing can trace every signal and backtest run to its exact source and instrument dataset.

### Negative / Trade-offs:
- Requires maintaining separate connectors and validation pipelines for Dukascopy, Yahoo, and Delta.
- Requires strategy and backtesting engines to explicitly consume instrument contract specifications (multipliers, tick sizes).

---

## 4. COMPLIANCE & VERIFICATION

1. In `src/core/constants.py`, instruments must be classified into `RESEARCH_INSTRUMENTS` and `EXECUTION_INSTRUMENTS`.
2. In `src/database/models/`, table `instruments` must record `domain` (`research` vs `execution`), `contract_multiplier`, and `settlement_asset`.
3. In `src/data/storage/`, Parquet file paths must be partitioned as `data/normalized/{source}/{instrument}/{timeframe}/`.
4. Automated tests must assert that backtests fail if an unrecognized instrument or mismatched specification is provided.

