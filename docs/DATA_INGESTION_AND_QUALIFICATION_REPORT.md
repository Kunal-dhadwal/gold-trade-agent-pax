# AGentTrXPAUS — Historical Gold Data Ingestion & Real-Data Qualification Report

**Milestone:** Post-2.5.4 Real-Data Qualification & Historical Ingestion  
**Audit Date:** 2026-10-12  
**Status Verdict:**  
- **Dataset Importer & Data Quality Infrastructure:** **PASS (ACCEPTED)**  
- **Multi-Year Strategy Candidate Qualification:** **FAIL (BLOCKED PENDING MULTI-YEAR DATA)**  
**Author:** Antigravity (Advanced Agentic Coding)  
**Governing Documents:**
- `PROJECT_CONSTITUTION.md` (Supreme Authority)
- `ARCHITECTURE.md`
- `docs/PHASE_2_SPEC.md`
- `docs/PRODUCT_ACCEPTANCE_REPORT.md`
- `ADR-008: Anti-Overfitting Architecture & OOS Quarantine`

---

## 1. Executive Summary & Verification Findings

This report delivers the evidence-based audit of historical gold data availability, data ingestion integrity, and real-market qualification status for the **AGentTrXPAUS Gold AI Strategy Creator**.

### Key Audit Findings

1. **Exhaustive Verification of Existing COMEX Gold Futures (`GC=F`) Dataset:**  
   The canonical Parquet file at `data/normalized/GC_F/1h.parquet` was audited down to individual byte checksums, timestamps, interval spacings, and OHLC geometric relationships.  
   - **Row Count:** Exactly 510 bars.  
   - **Chronological Span:** `2026-09-08 05:00:00+00:00` to `2026-10-08 05:21:56+00:00` (30.015 calendar days).  
   - **Parquet SHA-256:** `4a8cedf0e64e806f10f04293d45f728767fc666a92a84ccb4608e2ce3c8b1f13`.  
   - **Raw Source SHA-256:** `ee9d5ac7d8f46f73454c6abf61b410fd71adbd8aa26e4341c140418207b102de`.  
   - **Discrepancy Resolution:** Zero discrepancies exist between physical files, manifest, and dataset registry. The data represents **COMEX Gold Futures (`GC=F`)** over a 30-day window and is **not mislabeled as spot XAUUSD**.
2. **Absence of Local Multi-Year Spot Gold (`XAUUSD`):**  
   A comprehensive filesystem audit across the workspace confirmed that **no multi-year historical dataset for spot XAUUSD exists locally on disk**. The directory tree `data/raw/dukascopy/XAUUSD/2024/00/15` was found empty (0 bytes, 0 files). The only other real dataset present is a 164-bar archive of `PAXGUSDT` (`data/raw/delta/raw_delta_PAXGUSDT_1h_20261008_053350.json`).
3. **Sandbox Network Boundary & Compliant Ingestion:**  
   Standard sandbox isolation restricts external DNS resolution (`ConnectError: [Errno -3]`), preventing automated scraping from Dukascopy. Per constitutional mandates, network restrictions were **not bypassed**. Instead, the dedicated importer CLI ([`src/cli/import_dataset.py`](file:///home/epps/MyProject/AGentTrXPAUS/src/cli/import_dataset.py)) and loader ([`src/data/sources/local.py`](file:///home/epps/MyProject/AGentTrXPAUS/src/data/sources/local.py)) were hardened to accept external user-supplied CSV, Parquet, and JSON files across major institutional formats (including Dukascopy GMT Time and MetaTrader 5 separate Date/Time columns).
4. **Real-Data Tournament Outcome (Honest Fail-Closed Discipline):**  
   In a 10-candidate real-data tournament on the 510-bar `GC=F` dataset, **zero candidates passed mandatory validation gates**. All 10 failed `MIN_TRADES_GATE=50` (generating 0 to 13 trades on 408 In-Sample bars), and 2 duplicate ASTs were intercepted. Validation thresholds were strictly preserved; no artificial pass was fabricated.
5. **Regression Verification:**  
   **463 unit and integration tests PASSED**, 2 skipped (PostgreSQL tests requiring live database). Zero regressions.

---

## 2. Comprehensive Audit of Existing Real Datasets

### A. COMEX Gold Futures (`GC=F`) Physical Verification

| Metric / Property | Observed Value | Expected Manifest | Status |
| :--- | :--- | :--- | :---: |
| **Instrument Tag** | `GC=F` | `GC=F` | ✅ MATCH |
| **Timeframe** | `1h` | `1h` | ✅ MATCH |
| **Row Count** | `510` | `510` | ✅ MATCH |
| **Columns** | `timestamp, open, high, low, close, volume, source, instrument` | 8 required | ✅ MATCH |
| **Start Timestamp** | `2026-09-08 05:00:00+00:00` (UTC) | `2026-09-08T05:00:00+00:00` | ✅ MATCH |
| **End Timestamp** | `2026-10-08 05:21:56+00:00` (UTC) | `2026-10-08T05:21:56+00:00` | ✅ MATCH |
| **Timespan** | `30.015 calendar days` | ~30 days | ✅ MATCH |
| **Raw File Path** | `data/raw/yahoo/raw_GC_F_1h_20261008_053213.json` | Same | ✅ MATCH |
| **Raw File Size** | `49,032 bytes` | `49,032 bytes` | ✅ MATCH |
| **Raw SHA-256** | `ee9d5ac7d8f46f73454c6abf61b410fd71adbd8aa26e4341c140418207b102de` | Same | ✅ MATCH |
| **Parquet Path** | `data/normalized/GC_F/1h.parquet` | Same | ✅ MATCH |
| **Parquet File Size** | `23,736 bytes` | `23,736 bytes` | ✅ MATCH |
| **Parquet SHA-256** | `4a8cedf0e64e806f10f04293d45f728767fc666a92a84ccb4608e2ce3c8b1f13` | Same | ✅ MATCH |
| **Manifest Path** | `data/metadata/manifest_GC_F_1h.json` | Same | ✅ MATCH |
| **Data Quality Score** | `100.0 / 100.0` | `100.0` | ✅ MATCH |

#### Geometric and Temporal Consistency
- **Monotonic Progression:** `True` ($t_{i+1} > t_i$ strictly maintained).
- **Duplicate Timestamps:** `0`.
- **Null / NaN / Inf Fields:** `0` across all columns.
- **OHLC Geometry Violations:**
  - $High < Open$: `0`
  - $High < Close$: `0`
  - $High < Low$: `0`
  - $Low > Open$: `0`
  - $Low > Close$: `0`
  - Non-positive prices: `0`
  - Negative volumes: `0`
- **Interval Distribution:**
  - $1\text{ hour}$ ($3600\text{ s}$): 488 bars
  - $2\text{ hours}$ ($7200\text{ s}$): 16 bars (daily market maintenance breaks)
  - $2\text{ days } 2\text{ hours}$: 4 bars (standard weekend market closures)
  - $21\text{ min } 56\text{ s}$: 1 bar (final partial bar at snapshot timestamp)

### B. Delta Exchange Derivative (`PAXGUSDT`) Audit

| Metric / Property | Observed Value | Notes |
| :--- | :--- | :--- |
| **Instrument Tag** | `PAXGUSDT` | Derivative contract traded on Delta Exchange |
| **Timeframe** | `1h` | Left-closed hourly candles |
| **Row Count** | `164` | ~7 calendar days |
| **Start Timestamp** | `2026-10-01 10:00:00+00:00` | Oct 1, 2026 |
| **End Timestamp** | `2026-10-08 05:00:00+00:00` | Oct 8, 2026 |
| **Raw File Path** | `data/raw/delta/raw_delta_PAXGUSDT_1h_20261008_053350.json` | 16,270 bytes |
| **Raw SHA-256** | `518703014a0f44109fae4e5eb2d5612c6a49dbccbe638217bb4c9f13aa50b4ec` | Verified |
| **Canonical Parquet** | `data/normalized/PAXGUSDT/1h.parquet` | 7,946 bytes |
| **Parquet SHA-256** | `58501f67601f6b722391a45271138d82f9ec3bf36e7497c31ef777e8cfa4f3c7` | Verified |
| **Provenance Manifest** | `data/metadata/manifest_PAXGUSDT_1h.json` | Verified |
| **Data Quality Score** | `100.0 / 100.0` | Retained 164 / 164 bars |
| **Empirical Liquidity Finding** | `162 / 164 bars have Volume = 0` | Confirms Constitution Section 5.4 |

---

## 3. Local Multi-Year Spot Gold Availability Audit

An exhaustive filesystem search across `/home/epps/MyProject/AGentTrXPAUS` and the user home directory confirmed:
- `data/raw/dukascopy/XAUUSD/` contains an empty directory structure (`2024/00/15/` with 0 files).
- `data/metadata/checkpoints/checkpoint_XAUUSD_5m.json` contains a placeholder checkpoint (`2024-01-15T13:00:00+00:00`) generated during mock testing.
- **No multi-year spot XAUUSD data exists locally on disk.**

---

## 4. Exact Missing Data Requirements

To qualify candidate strategies under institutional anti-overfitting standards without compromising statistical rigor, the missing dataset must satisfy:

1. **Target Instrument:** Spot Gold (`XAUUSD`) or COMEX Gold Continuous Futures (`GC=F`).
2. **Timeframe:** `1h` (or `1m`/`5m` for automatic deterministic resampling).
3. **Temporal Depth & Bar Count:**
   - **Minimum Valid Bars:** $\ge 5,000$ hourly bars (~2 years of trading).
   - **Optimal Institutional Depth:** $10,000$ to $15,000$ hourly bars (~3.5 to 5 years).
   - **Mathematical Justification:**
     $$\text{IS Partition (80\%)} \ge 4,000\text{ bars}$$
     $$\text{Typical Hourly Swing Trade Frequency} = 0.015 \text{ to } 0.035 \text{ trades/bar}$$
     $$\text{Expected Trades} = 4,000 \times [0.015, 0.035] = 60 \text{ to } 140 \text{ trades} \ge \text{MIN\_TRADES\_GATE (50)}$$
   - On 408 In-Sample bars, 60+ trades requires an unrealistically high trade frequency ($> 0.15$ trades/bar), which forces overfitting and whipsaws.
4. **Mandatory Schema & Invariants:**
   - Columns: `timestamp`, `open`, `high`, `low`, `close`, `volume`.
   - UTC timezone (ISO 8601 or standard datetime string).
   - Price geometry: $High \ge \max(Open, Close, Low)$ and $Low \le \min(Open, Close, High)$.
   - Volume: $\ge 0$.
   - Quality score: $\ge 90.0 / 100.0$ via `DataQualityEngine`.

---

## 5. Practical & Compliant Acquisition & Import Procedure

Because sandbox network restrictions prohibit direct streaming from Dukascopy during agent execution, external data must be supplied via local file import.

### Step 1: External Acquisition (Outside Sandbox)
Obtain 3–5 years of hourly Spot Gold (`XAUUSD`) or Gold Futures (`GC=F`) data through one of the following compliant routes:
- **Dukascopy Web Export / QuantDataManager:** Export XAUUSD 1-hour BID bars from 2020 to 2024.
- **HistData.com Free ASCII Archive:** Free historical 1-minute or 1-hour ASCII data for XAUUSD.
- **MetaTrader 5 Historical Center Export:** Export XAUUSD H1 bars as CSV.
- Place the resulting file in the project directory, e.g.:  
  `data/raw/external/XAUUSD_1h_2020_2024.csv`

### Step 2: Certified Ingestion via CLI
Execute the dataset importer to audit, clean, and persist the dataset into canonical Parquet:
```bash
python -m src.cli.import_dataset \
  --file data/raw/external/XAUUSD_1h_2020_2024.csv \
  --instrument XAUUSD \
  --timeframe 1h \
  --source DUKASCOPY \
  --expected-frequency 3600 \
  --manifest-out data/metadata/manifest_XAUUSD_1h.json
```

**Supported Input Formats:**
- **Standard CSV:** Headers `timestamp,open,high,low,close,volume`.
- **Dukascopy CSV:** Headers `Gmt time,Open,High,Low,Close,Volume`.
- **MetaTrader CSV:** Separate `<DATE>` and `<TIME>` columns with `<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>`.
- **Parquet / Raw JSON:** Native `.parquet` or JSON arrays.

### Step 3: Qualification Tournament Execution
Once ingested, execute the 20-candidate qualification tournament:
```bash
python -m src.cli.strategy_creator \
  --instrument XAUUSD \
  --timeframe 1h \
  --candidates 20 \
  --output-dir ./output/tournament_xauusd_qualified \
  --provider mock \
  --format markdown
```

---

## 6. Real-Data Qualification Tournament (510-bar Real GC=F Data)

### Tournament Setup
- **Dataset:** `data/normalized/GC_F/1h.parquet` (510 hourly bars)
- **In-Sample Partition (80%):** 408 bars (2026-09-08 to 2026-10-01)
- **Holdout Partition (20%):** 102 bars (2026-10-01 to 2026-10-08) — strictly quarantined
- **Candidates Evaluated:** 10 diverse strategies across Trend Following, Breakout, and Mean Reversion

### Tournament Leaderboard

| Rank | Strategy Name | Archetype | In-Sample Trades | In-Sample Sharpe | Status | Rejection Reason |
| :---: | :--- | :--- | :---: | :---: | :---: | :--- |
| 1 | `XAU_MEAN_REVERSION_1H_3` | Mean Reversion | 5 | 0.17 | ❌ REJECTED | `Trade count 5 < minimum gate 50` |
| 2 | `XAU_MEAN_REVERSION_1H_6` | Mean Reversion | 0 | 0.00 | ❌ REJECTED | `Trade count 0 < minimum gate 50` |
| 3 | `XAU_BREAKOUT_1H_8` | Breakout | - | - | ❌ REJECTED | `DUPLICATE_STRATEGY: Exact AST match` |
| 4 | `XAU_MEAN_REVERSION_1H_9` | Mean Reversion | - | - | ❌ REJECTED | `DUPLICATE_STRATEGY: Exact AST match` |
| 5 | `XAU_TREND_FOLLOWING_1H_4` | Trend Following | 8 | -1.54 | ❌ REJECTED | `Trade count 8 < minimum gate 50` |
| 6 | `XAU_TREND_FOLLOWING_1H_10` | Trend Following | 4 | -4.01 | ❌ REJECTED | `Trade count 4 < minimum gate 50` |
| 7 | `XAU_BREAKOUT_1H_2` | Breakout | 13 | -4.69 | ❌ REJECTED | `Trade count 13 < minimum gate 50` |
| 8 | `XAU_TREND_FOLLOWING_1H_1` | Trend Following | 10 | -4.72 | ❌ REJECTED | `Trade count 10 < minimum gate 50` |
| 9 | `XAU_TREND_FOLLOWING_1H_7` | Trend Following | 5 | -6.81 | ❌ REJECTED | `Trade count 5 < minimum gate 50` |
| 10 | `XAU_BREAKOUT_1H_5` | Breakout | 7 | -7.78 | ❌ REJECTED | `Trade count 7 < minimum gate 50` |

### Key Observations
1. **Zero False Passes:** No strategies survived to the Holdout stage. Gates were not loosened.
2. **Holdout Quarantine Integrity:** The 102-bar Holdout partition was never accessed, ensuring zero contamination.
3. **Artifact Integrity:** All 10 validation dossiers and 10 TradingView Pine Script v6 files were generated in `output/tournament_real_gcf/`.
4. **Pine Script v6 Compilation Disclosure:** Internal AST structural verification passed. **No external TradingView cloud compilation is claimed**.

---

## 7. Institutional Governance & Test Evidence

| Safety Invariant | Status | Verification Evidence |
| :--- | :---: | :--- |
| `LIVE_TRADING_ENABLED = False` | ✅ ENFORCED | Verified in `src/core/config.py` |
| Decision A | ✅ OPEN | No capital allocated, zero live exchange orders |
| Broker / Exchange API Lockout | ✅ ENFORCED | Zero calls to Delta trading endpoints |
| Frozen Baseline Protection | ✅ ENFORCED | Phase 1–3 baselines untouched |
| Anti-Fabrication Rule | ✅ ENFORCED | Zero synthetic data passed off as real |
| Git Hygiene | ✅ ENFORCED | Zero unapproved git commits |

### Regression Suite Summary
```text
======================= test session starts =======================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
collected 465 items

463 passed, 2 skipped, 1 warning in 73.81s
```
- **CLI Integration Tests:** 14/14 PASSED
- **Total Tests Passed:** 463
- **Skipped:** 2 (PostgreSQL integration requiring live test database)
- **Regressions:** 0

---

## 8. Final Verdict & Milestone Status

| Deliverable / Capability | Verdict | Notes |
| :--- | :---: | :--- |
| **510-bar GC=F Dataset Audit** | **PASS** | 100% verified, zero discrepancies, exact SHA-256 match |
| **Dataset Importer Infrastructure** | **PASS** | Tested across CSV, Parquet, and JSON formats |
| **Data Quality & Ingestion Pipeline** | **PASS** | `DataQualityEngine` achieves 100.0/100.0 certification |
| **Multi-Year Spot XAUUSD Ingestion** | **BLOCKED** | Blocked pending external multi-year CSV/Parquet provision |
| **Real-Data Strategy Qualification** | **CONDITIONALLY BLOCKED** | Requires $\ge 5,000$ bars to satisfy `MIN_TRADES_GATE=50` |

### Overarching Status: **CONDITIONALLY ACCEPTED**
The platform infrastructure and ingestion tooling are fully production-ready. Advancing candidate strategies to verified Holdout champions requires supplying the multi-year external Gold dataset following the procedure outlined in Section 5.
