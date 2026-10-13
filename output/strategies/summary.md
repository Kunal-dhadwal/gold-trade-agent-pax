# AGentTrXPAUS — Strategy Creator Golden Run Summary

**Batch ID:** `gold_batch_20261012_125107`  
**Run Timestamp:** `2026-10-12T12:51:07.854897+00:00`  
**Instrument:** `XAUUSD` | **Timeframe:** `1h` | **LLM Provider:** `MOCK`  

---

## 1. Data Provenance & Boundary Evidence

| Metric | Value |
| :--- | :--- |
| **Data Source** | `RAW_YAHOO_ARCHIVE` |
| **File Path** | `/home/epps/MyProject/AGentTrXPAUS/data/raw/yahoo/raw_GC_F_1h_20261008_053213.json` |
| **SHA-256 Checksum** | `ee9d5ac7d8f46f73454c6abf61b410fd71adbd8aa26e4341c140418207b102de` |
| **Total Bars Loaded** | `510 bars` |
| **Chronological Span** | `2026-09-08T05:00:00+00:00 -> 2026-10-08T05:21:56+00:00` |
| **Synthetic Data Flag** | `NO (REAL-MARKET ARCHIVE)` |
| **Holdout Quarantine Ratio** | `20.0% (Chronologically Air-Gapped)` |

---

## 2. Tournament Leaderboard & Overfitting Controls

| Rank | Strategy Name | Tier | Status | IS Sharpe | Profit Factor | Max DD | CSS Score | DSR | Holdout Sharpe | Rejection Reason |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| 1 | `XAU_MEAN_REVERSION_1H_3` | `REJECTED` | ❌ REJECTED | 0.17 | 1.27 | 2.89% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 5 < minimum gate 50.; G... |
| 2 | `XAU_TREND_FOLLOWING_1H_4` | `REJECTED` | ❌ REJECTED | -1.54 | 1.21 | 3.24% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 8 < minimum gate 50.; G... |
| 3 | `XAU_BREAKOUT_1H_2` | `REJECTED` | ❌ REJECTED | -4.69 | 0.64 | 5.75% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 13 < minimum gate 50.; ... |
| 4 | `XAU_TREND_FOLLOWING_1H_1` | `REJECTED` | ❌ REJECTED | -4.72 | 0.54 | 5.77% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 10 < minimum gate 50.; ... |
| 5 | `XAU_BREAKOUT_1H_5` | `REJECTED` | ❌ REJECTED | -7.78 | 0.32 | 5.80% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 7 < minimum gate 50.; G... |

---

## 3. Executive Outcome

- **Candidates Requested:** 5
- **Candidates Generated:** 5
- **Surviving Strategies:** 0
- **Rejected Strategies:** 5
- **Tournament Champion:** `None` (Zero candidates passed all mandatory gates; fail-closed institutional discipline preserved)

---

## 4. Exported Artifact Directory Structure

All outputs exported to: `/home/epps/MyProject/AGentTrXPAUS/output/strategies`

- **Run Summary:** `summary.md`, `summary.json`
- **Leaderboard:** `leaderboard.json`
- **Validation Dossiers:** `dossiers/*.md` (5 files)
- **TradingView Pine Script v6:** `pine/*.pine` (5 files)

> [!IMPORTANT]
> TradingView Pine Script v6 scripts require independent Pine Editor compilation verification 
> on TradingView charts before any automated execution or alert integration.