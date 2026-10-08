# ADR-009: Walk-Forward Analysis, WFE Mathematical Edge Cases & Monte Carlo Stress Testing
**Status:** Approved & Frozen  
**Date:** 2026-10-08  
**Author:** Lead Quantitative Systems Engineer  
**Governing Documents:** `PROJECT_CONSTITUTION.md`, `ARCHITECTURE.md`, `ADR-004-ai-vs-deterministic-boundaries.md`

---

## 1. CONTEXT & PROBLEM STATEMENT

Static historical backtesting treats market history as a monolithic, stationary time series. In reality, Gold market dynamics, volatility regimes, central bank behaviors, and liquidity distribution shift across market cycles.

Two major methodological deficiencies compromise quantitative evaluation:
1. **Stationary Parameter Assumption:** Fixed parameters optimized across 5 years of data may perform well on aggregate while suffering catastrophic degradation during specific regime shifts.
2. **Path-Dependency Risk:** The chronological order of winning and losing trades produces a single realized equity curve. A slight perturbation in the sequence of returns could result in account liquidation or margin calls.
3. **Mathematical Edge Cases in Walk-Forward Efficiency (WFE):** The classic formula $\text{WFE} = \frac{R_{\text{OOS}}}{R_{\text{IS}}}$ is numerically undefined or dangerously misleading when In-Sample returns are non-positive ($R_{\text{IS}} \le 0$), producing division-by-zero errors or false positive ratios from two negative numbers (e.g. $\frac{-10\%}{-5\%} = +2.0$).

---

## 2. DECISION

We establish the **Walk-Forward Analysis (WFA) and Monte Carlo Stress Testing Framework**:

### A. Walk-Forward Analysis (WFA) Specification
WFA dynamically simulates how a strategy would have performed if re-evaluated iteratively through time.

```text
Time Horizon ─────────────────────────────────────────────────────────────────────────►
Fold 1: [--- Train: 12m ---] [- Test: 3m -] [--- OOS: 3m ---]
Fold 2:        [--- Train: 12m ---] [- Test: 3m -] [--- OOS: 3m ---]
Fold 3:               [--- Train: 12m ---] [- Test: 3m -] [--- OOS: 3m ---]
```

1. **Window Configuration:**
   - **Training Window (IS):** 12 months rolling.
   - **Optimization Window:** 3 months rolling.
   - **Out-of-Sample Window (OOS):** 3 months forward-anchored.
   - **Step Size:** 3 months.
2. **Leakage & Quarantine Invariant:**
   - The strategy parameter search grid is evaluated solely on the In-Sample window.
   - The optimal parameter set is then locked and executed across the forward OOS window with zero lookahead.
   - All forward OOS slices are stitched together chronologically to produce the Walk-Forward Equity Curve.
3. **Data Integrity Precondition:**
   - Each fold must possess at least 60 calendar days of valid market data. If a fold contains insufficient history, it is rejected as invalid.

---

### B. Walk-Forward Efficiency (WFE) Formulation & Edge Cases (P1-4 Resolution)

To guarantee mathematical correctness and prevent false positives, Walk-Forward Efficiency is represented uniformly across the entire system as a **decimal ratio** in $[0.0, \infty)$ (where `0.5000` corresponds to the 50% passing threshold).

#### 1. Mathematical Formulation
Let $R_{\text{IS}}$ be the In-Sample Annualized Return, $R_{\text{OOS}}$ be the Out-of-Sample Annualized Return, and $N_{\text{trades, OOS}}$ be the number of realized trades in the OOS period:

```text
IF R_IS <= 0.0:
    WFE = 0.0000
    WFE_GATE = FAIL
    (Rationale: An In-Sample losing or breakeven strategy cannot be evaluated for forward efficiency).

ELSE IF R_OOS <= 0.0 OR N_trades_OOS == 0:
    WFE = 0.0000
    WFE_GATE = FAIL
    (Rationale: The strategy was unprofitable in the OOS holdout or generated zero trades).

ELSE:
    WFE = round(R_OOS / R_IS, 4)
    IF WFE >= MIN_WFE_RATIO (Default: 0.5000):
        WFE_GATE = PASS
    ELSE:
        WFE_GATE = FAIL
```

#### 2. Numerical Precision & Floating-Point Handling
- **Representation:** Ratio in $[0.0, \infty)$ (never mixed with percentages in code or database columns).
- **Precision:** Stored as a floating-point number rounded to 4 decimal places (`round(wfe, 4)`).
- **NaN / Inf / Missing Handling:** If either $R_{\text{IS}}$ or $R_{\text{OOS}}$ is `NaN`, infinite, or missing, $\text{WFE} \equiv 0.0000$ and triggers an immediate `WFE_GATE = FAIL`.

#### 3. Deterministic Verification Examples
- **Example 1 (Standard Pass):**  
  $R_{\text{IS}} = +0.2000$ (+20%), $R_{\text{OOS}} = +0.1000$ (+10%)  
  $\implies \text{WFE} = \frac{0.1000}{0.2000} = \mathbf{0.5000} \implies$ **PASS** (meets $\ge 0.5000$ threshold).
- **Example 2 (Zero In-Sample Return):**  
  $R_{\text{IS}} = 0.0000$ (0%), $R_{\text{OOS}} = +0.0500$ (+5%)  
  $\implies \mathbf{WFE = 0.0000} \implies$ **FAIL** (avoids division by zero; In-Sample failed).
- **Example 3 (Both Returns Negative — Anti-False-Positive Guard):**  
  $R_{\text{IS}} = -0.1000$ (-10%), $R_{\text{OOS}} = -0.0500$ (-5%)  
  $\implies \mathbf{WFE = 0.0000} \implies$ **FAIL** (prevents $\frac{-0.05}{-0.10} = +0.50$ false positive).
- **Example 4 (Profitable In-Sample, Negative Out-of-Sample):**  
  $R_{\text{IS}} = +0.2000$ (+20%), $R_{\text{OOS}} = -0.0500$ (-5%)  
  $\implies \mathbf{WFE = 0.0000} \implies$ **FAIL**.
- **Example 5 (Strong Robustness):**  
  $R_{\text{IS}} = +0.2000$ (+20%), $R_{\text{OOS}} = +0.1200$ (+12%)  
  $\implies \text{WFE} = \frac{0.1200}{0.2000} = \mathbf{0.6000} \implies$ **PASS**.

---

### C. Monte Carlo Robustness Methodology

Monte Carlo analysis stress-tests candidate strategies across three distinct dimensions of uncertainty:

#### 1. Trade Sequence Reshuffling (Bootstrap Analysis)
- **Methodology:** Takes the array of $N$ realized trade returns from the backtest, samples with replacement across 2,000 synthetic simulations.
- **Metrics Calculated:**
  - $95^{\text{th}}$ percentile Maximum Drawdown ($\text{MaxDD}_{95\%}$).
  - Probability of reaching $20\%$ drawdown ($\mathbb{P}(\text{DD} \ge 20\%)$).
  - Maximum expected loss streak distribution.
- **Hard Gate:** If $\mathbb{P}(\text{DD} \ge 20\%) \ge \text{MAX\_RUIN\_PROBABILITY}$ (Default: 0.05), the strategy is rejected.
- **Reproducibility:** Pinned to a deterministic random seed (`seed = 42` or hash-derived seed).

#### 2. Execution Friction Perturbation Stress Test
- **Methodology:** Simulates deteriorating liquidity on Gold venues (e.g. news events, off-hours execution):
  - Slippage increased by $+50\%$ and $+100\%$.
  - Spread increased by $+50\%$ and $+100\%$.
- **Hard Gate:** The strategy must maintain a Gross Profit Factor $\text{GPF} \ge \text{MIN\_STRESS\_PROFIT\_FACTOR}$ (Default: 1.10) under $+50\%$ frictional stress.

#### 3. Parameter Neighborhood Stability Test
- **Methodology:** Perturbs each continuous parameter by $\pm 10\%$ and $\pm 20\%$ within its defined neighborhood.
- **Economic Principle:** Robust strategies occupy wide, flat parameter plateaus. Overfit strategies occupy narrow, fragile knife-edge peaks.
- **Hard Gate:** If a $\pm 10\%$ shift causes an aggregate performance degradation exceeding $\text{MAX\_PARAM\_DEGRADATION}$ (Default: 0.40), the strategy is rejected as fragile.

#### 4. Scope and Limitations:
- Monte Carlo evaluates path-independent distributional risk and execution robustness.
- **It does NOT predict future profitability, future market regimes, or eliminate market risk.**

---

## 3. CONSEQUENCES

### Positive:
- **Numerical Robustness:** Eliminates division-by-zero errors and false-positive passes from negative In-Sample returns.
- **Consistent Representation:** Decimal ratio convention prevents conversion errors between spec, database columns, and scoring math.
- **Comprehensive Tail Testing:** Confirms that candidate strategies can survive random trade ordering and realistic execution deterioration.

### Negative / Trade-offs:
- Strict WFE logic immediately rejects any strategy unable to produce positive In-Sample returns. (This is an intended benefit).

---

## 4. COMPLIANCE & VERIFICATION
1. Unit tests in `tests/unit/validation/test_walk_forward.py` must test all 5 deterministic WFE edge cases and verify exact float outputs matching the specification.
2. Monte Carlo tests must verify that setting `seed = 42` produces bit-for-bit identical bootstrap results across runs.
