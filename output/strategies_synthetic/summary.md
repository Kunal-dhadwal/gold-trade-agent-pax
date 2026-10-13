# AGentTrXPAUS — Strategy Creator Golden Run Summary

**Batch ID:** `gold_batch_20261012_125128`  
**Run Timestamp:** `2026-10-12T12:51:44.591006+00:00`  
**Instrument:** `XAUUSD` | **Timeframe:** `1h` | **LLM Provider:** `MOCK`  

---

## 1. Data Provenance & Boundary Evidence

| Metric | Value |
| :--- | :--- |
| **Data Source** | `SYNTHETIC_BENCHMARK` |
| **File Path** | `N/A (Generated Memory)` |
| **SHA-256 Checksum** | `N/A` |
| **Total Bars Loaded** | `12000 bars` |
| **Chronological Span** | `2023-01-01T00:00:00+00:00 -> 2024-05-14T23:00:00+00:00` |
| **Synthetic Data Flag** | `YES (BENCHMARK SMOKE)` |
| **Holdout Quarantine Ratio** | `20.0% (Chronologically Air-Gapped)` |

---

## 2. Tournament Leaderboard & Overfitting Controls

| Rank | Strategy Name | Tier | Status | IS Sharpe | Profit Factor | Max DD | CSS Score | DSR | Holdout Sharpe | Rejection Reason |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| 1 | `XAU_TREND_FOLLOWING_1H_4` | `REJECTED` | ❌ REJECTED | 54.94 | 99.00 | 0.11% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 10 < minimum gate 50. |
| 2 | `XAU_TREND_FOLLOWING_1H_1` | `REJECTED` | ❌ REJECTED | 53.74 | 99.00 | 0.11% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 11 < minimum gate 50. |
| 3 | `XAU_BREAKOUT_1H_5` | `REJECTED` | ❌ REJECTED | 0.00 | 0.00 | 0.00% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 0 < minimum gate 50.; G... |
| 4 | `XAU_MEAN_REVERSION_1H_3` | `REJECTED` | ❌ REJECTED | -5.47 | 0.56 | 25.65% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Trade count 40 < minimum gate 50.; ... |
| 5 | `XAU_BREAKOUT_1H_2` | `REJECTED` | ❌ REJECTED | -9.33 | 0.31 | 48.15% | 0.0 | 0.00 | [NOT_EVALUATED] | IN_SAMPLE_HARD_GATES: Gross Profit Factor 0.31 < minimum ... |

---

## 3. Executive Outcome

- **Candidates Requested:** 5
- **Candidates Generated:** 5
- **Surviving Strategies:** 0
- **Rejected Strategies:** 5
- **Tournament Champion:** `None` (Zero candidates passed all mandatory gates; fail-closed institutional discipline preserved)

---

## 4. Exported Artifact Directory Structure

All outputs exported to: `/home/epps/MyProject/AGentTrXPAUS/output/strategies_synthetic`

- **Run Summary:** `summary.md`, `summary.json`
- **Leaderboard:** `leaderboard.json`
- **Validation Dossiers:** `dossiers/*.md` (5 files)
- **TradingView Pine Script v6:** `pine/*.pine` (5 files)

> [!IMPORTANT]
> TradingView Pine Script v6 scripts require independent Pine Editor compilation verification 
> on TradingView charts before any automated execution or alert integration.