# AGentTrXPAUS — Strategy Creator Golden Run Summary

**Batch ID:** `gold_batch_20261012_130724`  
**Run Timestamp:** `2026-10-12T13:07:25.777322+00:00`  
**Instrument:** `GC=F` | **Timeframe:** `1h` | **LLM Provider:** `MOCK`  

---

## 1. Data Provenance & Boundary Evidence

| Metric | Value |
| :--- | :--- |
| **Data Source** | `PARQUET_STORAGE` |
| **File Path** | `/home/epps/MyProject/AGentTrXPAUS/data/normalized/GC_F/1h.parquet` |
| **SHA-256 Checksum** | `4a8cedf0e64e806f10f04293d45f728767fc666a92a84ccb4608e2ce3c8b1f13` |
| **Total Bars Loaded** | `510 bars` |
| **Chronological Span** | `2026-09-08T05:00:00+00:00 -> 2026-10-08T05:21:56+00:00` |
| **Synthetic Data Flag** | `NO (REAL-MARKET ARCHIVE)` |
| **Holdout Quarantine Ratio** | `20.0% (Chronologically Air-Gapped)` |

---

## 2. Tournament Leaderboard & Overfitting Controls

| Rank | Strategy Name | Tier | Status | IS Sharpe | Profit Factor | Max DD | CSS Score | DSR | Holdout Sharpe | Rejection Reason |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| 1 | `XAU_MEAN_REVERSION_1H_3` | `REJECTED` | ❌ REJECTED | 0.17 | 1.27 | 2.89% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 5 < minimum gate 50.; G... |
| 2 | `XAU_MEAN_REVERSION_1H_6` | `REJECTED` | ❌ REJECTED | 0.00 | 0.00 | 0.00% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 0 < minimum gate 50.; G... |
| 3 | `XAU_BREAKOUT_1H_8` | `REJECTED` | ❌ REJECTED | 0.00 | 0.00 | 0.00% | 0.0 | 0.00 | [NOT_EVALUATED] | DUPLICATE_STRATEGY: Exact AST matches earlier candidate 8... |
| 4 | `XAU_MEAN_REVERSION_1H_9` | `REJECTED` | ❌ REJECTED | 0.00 | 0.00 | 0.00% | 0.0 | 0.00 | [NOT_EVALUATED] | DUPLICATE_STRATEGY: Exact AST matches earlier candidate c... |
| 5 | `XAU_TREND_FOLLOWING_1H_4` | `REJECTED` | ❌ REJECTED | -1.54 | 1.21 | 3.24% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 8 < minimum gate 50.; G... |
| 6 | `XAU_TREND_FOLLOWING_1H_10` | `REJECTED` | ❌ REJECTED | -4.01 | 0.60 | 3.20% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 4 < minimum gate 50.; G... |
| 7 | `XAU_BREAKOUT_1H_2` | `REJECTED` | ❌ REJECTED | -4.69 | 0.64 | 5.75% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 13 < minimum gate 50.; ... |
| 8 | `XAU_TREND_FOLLOWING_1H_1` | `REJECTED` | ❌ REJECTED | -4.72 | 0.54 | 5.77% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 10 < minimum gate 50.; ... |
| 9 | `XAU_TREND_FOLLOWING_1H_7` | `REJECTED` | ❌ REJECTED | -6.81 | 0.00 | 6.25% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 5 < minimum gate 50.; G... |
| 10 | `XAU_BREAKOUT_1H_5` | `REJECTED` | ❌ REJECTED | -7.78 | 0.32 | 5.80% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 7 < minimum gate 50.; G... |

---

## 3. Executive Outcome

- **Candidates Requested:** 10
- **Candidates Generated:** 10
- **Surviving Strategies:** 0
- **Rejected Strategies:** 10
- **Tournament Champion:** `None` (Zero candidates passed all mandatory gates; fail-closed institutional discipline preserved)

---

## 4. Exported Artifact Directory Structure

All outputs exported to: `/home/epps/MyProject/AGentTrXPAUS/output/tournament_real_gcf`

- **Run Summary:** `summary.md`, `summary.json`
- **Leaderboard:** `leaderboard.json`
- **Validation Dossiers:** `dossiers/*.md` (10 files)
- **TradingView Pine Script v6:** `pine/*.pine` (10 files)

> [!IMPORTANT]
> TradingView Pine Script v6 scripts require independent Pine Editor compilation verification 
> on TradingView charts before any automated execution or alert integration.