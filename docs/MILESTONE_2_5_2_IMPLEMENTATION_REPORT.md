# AGentTrXPAUS — Milestone 2.5.2 Implementation Report
## Deflated Sharpe Ratio (DSR), Overfitting Controls & Strategy Tournament

**Document Version:** 1.0.0  
**Date:** 2026-10-12  
**Status:** COMPLETE / PASS  
**Governing Documents:** `PROJECT_CONSTITUTION.md`, `ARCHITECTURE.md`, `docs/PHASE_2_SPEC.md`, `docs/ADR/ADR-008-anti-overfitting-and-validation-gates.md`, `docs/PRODUCT_OBJECTIVE_ALIGNMENT_AUDIT.md`  
**Working Branch:** `release1.1v`  
**Execution Environment:** Linux (x86_64), Python 3.12.3, pytest 9.1.1  

---

## 1. Executive Summary

Milestone 2.5.2 implements the **Deflated Sharpe Ratio (DSR), Quantitative Overfitting Controls, Quarantined Holdout Gate, and Strategy Tournament Manager** for AGentTrXPAUS.

In alignment with the core project objective re-established by the **Product Objective Alignment Audit (2026-10-12)**, AGentTrXPAUS is fundamentally an **AI Gold Quantitative Strategy Creator**. This milestone equips the system to evaluate dozens of candidate strategy permutations without falling victim to multiple-testing bias, data snooping, or curve fitting.

### Milestone 2.5.2 Deliverables Status:
1. **Deflated Sharpe Ratio & Probabilistic Sharpe Ratio Engine (`src/quant/validation/overfitting.py`)**: Implements Bailey & López de Prado (2014) formulation accounting for observed Sharpe, sample size, non-normality (skewness and kurtosis), and cumulative trials count ($N$).
2. **Dedicated Overfitting Control Module (`src/quant/validation/overfitting.py`)**: Provides AST deduplication fingerprinting (SHA-256), trial budget tracking, and empirical variance calculation across candidate sets.
3. **Quarantined 20% Holdout Partitioning & Terminal Gate (`HoldoutValidationGate`)**: Enforces strict chronological separation ($max(train) < min(holdout)$) with zero leakage to ideation or parameter tuning.
4. **Strategy Tournament Manager (`TournamentManager`)**: Ingests candidate proposals, enforces cascading validation gates (with strict $CSS = 0.0$ on failure), applies DSR multiple-testing penalties, tracks candidate lineage, and outputs a deterministic ranked leaderboard with explanations.
5. **Multi-Batch Persistence**: Guarantees trial counts ($N$) persist across batches and are never silently reset.
6. **Unit & Numerical Test Suite (`tests/unit/quant/test_overfitting_and_tournament.py`)**: 22 comprehensive tests covering all statistical edge cases, asymptotic bounds, holdout isolation, deduplication, and tournament ranking.
7. **Full Repository Regression Verification**: All 439 repository tests passing cleanly (2 skipped due to external PostgreSQL dependencies, 0 failures, exit code 0).

**Overall Milestone Verdict: PASS**

---

## 2. Inventory of Implemented Components

| Component / Artifact | File Location | Responsibility | Status |
| :--- | :--- | :--- | :--- |
| **Statistical Overfitting Engine** | [`src/quant/validation/overfitting.py`](file:///home/epps/MyProject/AGentTrXPAUS/src/quant/validation/overfitting.py) | Implements DSR, PSR, expected maximum Sharpe under $H_0$, Sharpe standard error under non-normality, and return-series processing. | **VERIFIED** |
| **AST Deduplication Fingerprinter** | [`src/quant/validation/overfitting.py`](file:///home/epps/MyProject/AGentTrXPAUS/src/quant/validation/overfitting.py) (`compute_strategy_hash`) | Computes deterministic canonical SHA-256 hash of strategy AST rules, indicators, and parameters to block duplicate trials. | **VERIFIED** |
| **Holdout Partition & Gatekeeper** | [`src/quant/validation/overfitting.py`](file:///home/epps/MyProject/AGentTrXPAUS/src/quant/validation/overfitting.py) (`HoldoutValidationGate`) | Slices strictly chronological 80/20 train/holdout partitions and evaluates candidates on holdout *only* after surviving earlier gates. | **VERIFIED** |
| **Tournament Manager** | [`src/quant/validation/overfitting.py`](file:///home/epps/MyProject/AGentTrXPAUS/src/quant/validation/overfitting.py) (`TournamentManager`) | Multi-candidate tournament orchestrator tracking lineage, cumulative trials, cascading rejections, and ranked leaderboards. | **VERIFIED** |
| **AI Tournament Module Interface** | [`src/ai/tournament.py`](file:///home/epps/MyProject/AGentTrXPAUS/src/ai/tournament.py) | Exports tournament management types and functions for multi-agent strategy research workflows. | **VERIFIED** |
| **Validation Policy Thresholds** | [`src/quant/validation/config.py`](file:///home/epps/MyProject/AGentTrXPAUS/src/quant/validation/config.py) | Added configurable institutional thresholds: `MIN_HOLDOUT_SHARPE` (1.0), `MAX_HOLDOUT_DRAWDOWN` (20%), `MIN_HOLDOUT_TRADES` (15), `MIN_DSR_THRESHOLD` (0.95), and `HOLDOUT_RATIO` (0.20). | **VERIFIED** |
| **Unit & Numerical Test Suite** | [`tests/unit/quant/test_overfitting_and_tournament.py`](file:///home/epps/MyProject/AGentTrXPAUS/tests/unit/quant/test_overfitting_and_tournament.py) | 22 targeted tests covering statistical formulas, edge cases, deduplication, holdout isolation, and ranking determinism. | **VERIFIED** |

---

## 3. Statistical Formulation & Numerical Assumptions

### 3.1 Deflated Sharpe Ratio (Bailey & López de Prado 2014)
The Deflated Sharpe Ratio computes the probability that the observed Sharpe ratio $\widehat{SR}$ is greater than zero after correcting for two distortions:
1. **Selection Bias under Multiple Testing:** Searching over $N$ strategy permutations inflates the maximum observed Sharpe ratio by chance.
2. **Non-Normality of Returns:** Skewed returns ($\gamma_3$) and fat tails / excess kurtosis ($\gamma_4$) distort standard error estimates.

#### A. Expected Maximum Sharpe Ratio Under $H_0$ ($SR^*$)
Under Extreme Value Theory (EVT), for $N$ independent Gaussian random variables with variance $V$ (where $V$ is the variance of Sharpe ratios across the trial pool):
$$E[\max_N] = \sqrt{V} \cdot \left[ (1 - \gamma_E) \Phi^{-1}\left(1 - \frac{1}{N}\right) + \gamma_E \Phi^{-1}\left(1 - \frac{1}{N \cdot e}\right) \right]$$
where:
- $\gamma_E \approx 0.5772156649$ is the Euler-Mascheroni constant.
- $e \approx 2.7182818284$.
- $\Phi^{-1}$ is the inverse standard normal cumulative distribution function (quantile/probit function).
- When $N = 1$ or $V = 0.0$, $E[\max_1] = 0.0$.

#### B. Asymptotic Standard Error of $\widehat{SR}$ Under Non-Normality
Following Mertens (2002) and Lo (2002):
$$\sigma_{\widehat{SR}} = \sqrt{\frac{1 - \gamma_3 \widehat{SR} + \frac{\gamma_4 - 1}{4} \widehat{SR}^2}{T - 1}}$$
where:
- $T$ is the sample size (number of observations).
- $\gamma_3$ is the skewness of returns.
- $\gamma_4$ is Pearson's kurtosis of returns (normal distribution $\gamma_4 = 3.0$).
- When $\gamma_3 = 0.0$ and $\gamma_4 = 3.0$ (normal distribution), this reduces to Lo (2002): $\sigma_{\widehat{SR}} = \sqrt{\frac{1 + \frac{1}{2} \widehat{SR}^2}{T - 1}}$.

#### C. Deflated Sharpe Ratio (DSR) & Probabilistic Sharpe Ratio (PSR)
$$z = \frac{\widehat{SR} - E[\max_N]}{\sigma_{\widehat{SR}}}$$
$$DSR = \Phi(z)$$
where $\Phi$ is the standard normal cumulative distribution function.
- $DSR \in (0.0, 1.0)$ represents the statistical confidence that the strategy's observed edge is genuine after accounting for $N$ trials.
- Threshold: $DSR \ge 0.95$ ($\alpha = 0.05$ significance).

### 3.2 Documented Numerical Edge Cases & Protections
1. **$T \le 1$:** Fails closed ($DSR = 0.0, passed = False$). Sample size is insufficient for central limit theorem.
2. **Zero Returns Variance ($\sigma \le 10^{-12}$):** Fails closed ($DSR = 0.0, passed = False$). Flat equity lines produce undefined Sharpe ratios.
3. **Negative Variance or Negative Term Under Square Root:** Clamped safely to $10^{-12}$ to prevent imaginary numbers from pathological sample moments.
4. **Astronomical Trial Counts ($N \gg 10^6$):** Probabilities $1 - 1/N$ are clamped to $[10^{-15}, 1 - 10^{-15}]$ to avoid floating-point overflow or infinity in `scipy.stats.norm.ppf`.
5. **Empirical Variance Calculation:** If a list of trial Sharpe ratios is provided, $V$ is computed as the sample variance ($\text{ddof}=1$). If $N=1$, $V=0.0$.

---

## 4. Quarantined 20% Holdout Partitioning & Gate

To prevent subtle forward-looking data contamination:
1. **Strict Chronological Slicing:** The historical dataset is sorted chronologically and partitioned:
   - First 80%: Training / In-Sample dataset (`train_df`), utilized for In-Sample backtests, Walk-Forward Analysis (WFA), parameter plateaus, and Monte Carlo simulations.
   - Final 20%: Quarantined Holdout dataset (`holdout_df`), permanently air-gapped from ideation prompts, parameter tuning, and intermediate scoring.
2. **Leakage Verification:** Asserted via `train_df['timestamp'].max() < holdout_df['timestamp'].min()`.
3. **Cascading Holdout Execution:** The `HoldoutValidationGate` is evaluated **only** after a strategy proposal passes all In-Sample hard gates, WFA, and Monte Carlo tests. If a strategy fails any earlier gate, the holdout partition is never touched for that strategy.
4. **Holdout Gate Criteria:**
   - Trade Count $\ge 15$.
   - Sharpe Ratio $\ge 1.0$.
   - Max Drawdown $\le 20.0\%$.
   - Net Profit $> \$0.00$.

---

## 5. Strategy Tournament Manager (`TournamentManager`)

The `TournamentManager` orchestrates multi-candidate evaluations:
1. **Cumulative Trial Budget Tracking:**
   - Tracks `_total_trials_evaluated`.
   - Never resets trial counts between batches. Trial counts accumulate across batch 1, batch 2, etc., ensuring multiple-testing penalties reflect total research exploration.
   - Resetting requires explicit operator confirmation (`reset_trials(confirm_reset=True)`).
2. **Cryptographic AST Deduplication:**
   - `compute_strategy_hash` computes canonical SHA-256 hashes of strategy conditions, indicators, parameters, and directions.
   - Exact duplicate submissions are immediately rejected as `DUPLICATE_STRATEGY` without consuming compute or inflating trial counts.
3. **Hard Gate Integrity & Invariant Preservation:**
   - If a candidate fails any mandatory gate (trade count, profit factor, max drawdown, WFE, Monte Carlo ruin probability, holdout gate, or DSR):
     - CSS is strictly set to $0.00$.
     - Candidate is assigned `DISQUALIFIED` or `REJECTED`.
     - CSS possesses zero authority to resurrect a rejected candidate.
4. **Deterministic Ranking & Winner Selection:**
   - Surviving candidates are ranked deterministically:
     1. Gate pass status (`passed_all_gates: True > False`).
     2. Composite Strategy Score (`css_score` descending).
     3. Deflated Sharpe Ratio (`dsr_score` descending).
     4. Sortino Ratio (`sortino_ratio` descending).
     5. Alphanumeric Strategy ID ascending (stable tie-breaker).
   - Rank 1 candidate achieving Tier 1 status is designated `TIER_1_WINNER`.
5. **Candidate Lineage Tracking:**
   - Records `hypothesis_id`, `parent_strategy_id`, generation number, and AST hash on every leaderboard entry.

---

## 6. Test Evidence & Regression Audit

### 6.1 Targeted Unit & Numerical Tests (`tests/unit/quant/test_overfitting_and_tournament.py`)
```bash
./.venv/bin/pytest -v tests/unit/quant/test_overfitting_and_tournament.py
```
**Results:**
```text
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/epps/MyProject/AGentTrXPAUS
configfile: pyproject.toml
plugins: asyncio-1.4.0, anyio-4.15.1, cov-7.1.0
collected 22 items

tests/unit/quant/test_overfitting_and_tournament.py::test_expected_max_sharpe_n1_zero PASSED [  4%]
tests/unit/quant/test_overfitting_and_tournament.py::test_expected_max_sharpe_monotonic_increase PASSED [  9%]
tests/unit/quant/test_overfitting_and_tournament.py::test_expected_max_sharpe_variance_scaling PASSED [ 13%]
tests/unit/quant/test_overfitting_and_tournament.py::test_sharpe_std_error_normal_vs_nonnormal PASSED [ 18%]
tests/unit/quant/test_overfitting_and_tournament.py::test_calculate_psr_behavior PASSED [ 22%]
tests/unit/quant/test_overfitting_and_tournament.py::test_calculate_dsr_single_vs_multiple_trials PASSED [ 27%]
tests/unit/quant/test_overfitting_and_tournament.py::test_calculate_dsr_extreme_numerical_bounds PASSED [ 31%]
tests/unit/quant/test_overfitting_and_tournament.py::test_calculate_dsr_from_returns_empirical PASSED [ 36%]
tests/unit/quant/test_overfitting_and_tournament.py::test_calculate_dsr_from_constant_returns PASSED [ 40%]
tests/unit/quant/test_overfitting_and_tournament.py::test_ast_hash_identical_for_equivalent_strategies PASSED [ 45%]
tests/unit/quant/test_overfitting_and_tournament.py::test_ast_hash_distinct_for_different_parameters PASSED [ 50%]
tests/unit/quant/test_overfitting_and_tournament.py::test_partition_train_holdout_strict_chronological_isolation PASSED [ 54%]
tests/unit/quant/test_overfitting_and_tournament.py::test_partition_train_holdout_insufficient_bars_rejected PASSED [ 59%]
tests/unit/quant/test_overfitting_and_tournament.py::test_holdout_gate_rejection_low_trades PASSED [ 63%]
tests/unit/quant/test_overfitting_and_tournament.py::test_tournament_trial_count_persists_across_batches PASSED [ 68%]
tests/unit/quant/test_overfitting_and_tournament.py::test_tournament_deduplication PASSED [ 72%]
tests/unit/quant/test_overfitting_and_tournament.py::test_tournament_hard_gate_failure_zero_css PASSED [ 77%]
tests/unit/quant/test_overfitting_and_tournament.py::test_tournament_reset_requires_confirmation PASSED [ 81%]
tests/unit/quant/test_overfitting_and_tournament.py::test_tournament_ranking_deterministic_and_winner PASSED [ 86%]
tests/unit/quant/test_overfitting_and_tournament.py::test_holdout_gate_rejection_low_sharpe PASSED [ 90%]
tests/unit/quant/test_overfitting_and_tournament.py::test_tournament_candidate_lineage_tracking PASSED [ 95%]
tests/unit/quant/test_overfitting_and_tournament.py::test_dsr_with_empirical_trial_sharpes PASSED [100%]

============================== 22 passed in 8.11s ==============================
```

### 6.2 Full Repository Regression Verification
```bash
./.venv/bin/pytest
```
**Results:**
```text
================================================= test session starts ==================================================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/epps/MyProject/AGentTrXPAUS
configfile: pyproject.toml
testpaths: tests
plugins: asyncio-1.4.0, anyio-4.15.1, cov-7.1.0
collected 441 items

[... 439 passed across all modules ...]

================== 439 passed, 2 skipped, 1 warning in 36.62s ==================
```
- **Total Tests Passed:** 439
- **Total Tests Skipped:** 2 (Disclose: integration tests requiring a dedicated PostgreSQL container instance)
- **Total Tests Failed:** 0
- **Regressions:** 0

---

## 7. Safety Invariants & Governance Compliance

| Governance Constraint | Verification Status | Evidence |
| :--- | :--- | :--- |
| **`LIVE_TRADING_ENABLED = False`** | **LOCKED** | Hard-coded in [`src/core/config.py`](file:///home/epps/MyProject/AGentTrXPAUS/src/core/config.py#L28). |
| **Decision A Status** | **OPEN** | No external Delta Testnet or Live exchange keys configured or accessed. |
| **No Outbound Network Calls** | **ENFORCED** | Overfitting and tournament calculations operate 100% locally offline. |
| **Frozen Phase 1–3 Files** | **UNTOUCHED** | Zero modifications to [`src/core/config.py`](file:///home/epps/MyProject/AGentTrXPAUS/src/core/config.py), [`src/risk/kill_switch.py`](file:///home/epps/MyProject/AGentTrXPAUS/src/risk/kill_switch.py), or [`src/risk/paper_risk_engine.py`](file:///home/epps/MyProject/AGentTrXPAUS/src/risk/paper_risk_engine.py). |
| **Git Working Tree Hygiene** | **COMPLIANT** | Zero commits, tags, pushes, or resets performed during this milestone. |
| **Milestone Scope Integrity** | **BOUNDED** | Scope strictly restricted to Milestone 2.5.2 (Milestones 2.5.3 and 2.5.4 remain untouched). |

---

## 8. Conclusion & Sign-Off

Milestone 2.5.2 has successfully delivered the Deflated Sharpe Ratio calculation engine, dedicated overfitting controls, 20% quarantined holdout gate, and strategy tournament manager. The implementation satisfies all statistical, mathematical, and architectural requirements with zero regressions across the codebase.

**Final Sub-Milestone Verdict: PASS**

