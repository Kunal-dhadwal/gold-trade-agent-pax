# ADR-010: Composite Strategy Scoring Framework & Ranking Methodology
**Status:** Approved & Frozen  
**Date:** 2026-10-08  
**Author:** Lead Quantitative Systems Engineer  
**Governing Documents:** `PROJECT_CONSTITUTION.md`, `ARCHITECTURE.md`, `ADR-004-ai-vs-deterministic-boundaries.md`

---

## 1. CONTEXT & PROBLEM STATEMENT

When multiple candidate strategies pass the initial validation gates, the system requires an objective, deterministic method to compare, rank, and select top candidates for Phase 3 paper trading.

Relying on subjective LLM evaluations (e.g. *"The AI prefers Strategy A because of its elegant logic"*) violates the core project constitution. Similarly, relying on a single isolated metric (such as Sharpe ratio or Net PnL) encourages selecting high-turnover or curve-fitted strategies with unaddressed tail risks.

---

## 2. DECISION

We formally establish the **Deterministic Composite Strategy Scoring (CSS) Framework**:

### A. Mathematical Formula
Every strategy surviving all prior validation gates receives a deterministic score between $0.0$ and $100.0$:

$$\text{CSS} = \left( \sum_{i=1}^{6} w_i \cdot S_i \right) \times 100$$

Where $w_i$ represents the institutional weights ($\sum w_i = 1.0$), and $S_i \in [0.0, 1.0]$ represents the normalized component score:

```text
+---+-----------------------------+--------+-------------------------------------------------------+
| i | Component                   | Weight | Scoring Normalization Formula                         |
+---+-----------------------------+--------+-------------------------------------------------------+
| 1 | OOS Risk-Adjusted Return    | 0.25   | S_1 = min(1.0, max(0.0, Sortino_OOS / 3.0))           |
| 2 | Maximum Drawdown Penalty    | 0.20   | S_2 = max(0.0, 1.0 - (MaxDD_OOS / 0.15))              |
| 3 | Gross Profit Factor Quality | 0.15   | S_3 = min(1.0, max(0.0, (GPF_OOS - 1.0) / 1.5))       |
| 4 | Walk-Forward Efficiency     | 0.15   | S_4 = min(1.0, max(0.0, WFE / 1.0))                   |
| 5 | Monte Carlo Ruin Defense    | 0.15   | S_5 = max(0.0, 1.0 - (MC_MaxDD_95 / 0.20))            |
| 6 | Parameter Flatness          | 0.10   | S_6 = max(0.0, 1.0 - (Degradation_10pct / 0.40))      |
+---+-----------------------------+--------+-------------------------------------------------------+
```

### B. Scoring Breakdown & Weight Justification
1. **$w_1 = 0.25$ (OOS Sortino):** Prioritizes downside risk-adjusted return over pure Sharpe ratio to prevent penalizing upside volatility.
2. **$w_2 = 0.20$ (Max Drawdown Penalty):** Capital preservation is paramount. A strategy with a 15% drawdown receives 0.0 for this component.
3. **$w_3 = 0.15$ (Gross Profit Factor Quality):** Rewards strategies with a robust gross edge over transaction costs.
4. **$w_4 = 0.15$ (Walk-Forward Efficiency):** Rewards consistency across time and penalizes in-sample over-optimization. WFE is evaluated strictly as a ratio where $1.0$ (100% efficiency) earns full score.
5. **$w_5 = 0.15$ (Monte Carlo Ruin Defense):** Evaluates path-independent tail robustness under trade order permutation.
6. **$w_6 = 0.10$ (Parameter Flatness):** Rewards parameter stability and penalizes fragile point-optimized spikes.

### C. Tier Classification & Progression Rules
Candidate strategies are classified according to their final CSS score against configurable policy thresholds (`CSS_TIER1_THRESHOLD = 80.0`, `CSS_TIER2_THRESHOLD = 70.0`):

- **$\text{CSS} \ge \text{CSS\_TIER1\_THRESHOLD}$ (80.0) $\longrightarrow$ TIER 1 (PRIME CANDIDATE):** Eligible for priority scheduling into Phase 3 Paper Trading.
- **$\text{CSS\_TIER2\_THRESHOLD} \le \text{CSS} < \text{CSS\_TIER1\_THRESHOLD}$ (70.0 to 79.9) $\longrightarrow$ TIER 2 (ACCEPTABLE CANDIDATE):** Viable candidate held in research reserve.
- **$50.0 \le \text{CSS} < \text{CSS\_TIER2\_THRESHOLD}$ $\longrightarrow$ TIER 3 (MARGINAL / RETRY):** Diagnostic feedback routed to AI Strategy Researcher for refinement.
- **$\text{CSS} < 50.0$ $\longrightarrow$ DISQUALIFIED / REJECTED:** Strategy archived with rejection rationale.

### D. Hard Gate Veto Invariant (P2-3 Alignment)
- **CSS cannot resurrect a failed strategy.** CSS is computed strictly after a strategy has satisfied all Tier-1 hard rejection gates, Walk-Forward Efficiency ($\text{WFE} \ge 0.50$), and Monte Carlo stress tests.
- If a strategy fails any mandatory gate, its validation status transitions immediately to `REJECTED`, and CSS calculation is skipped.

---

## 3. CONSEQUENCES

### Positive:
- **Zero Subjectivity:** Eliminates all ambiguity in strategy selection; ranking is 100% auditable and reproducible.
- **Multi-Factor Balance:** Penalizes strategies that achieve high returns at the expense of fragile parameters or excessive drawdown.
- **Automated Feedback Loop:** Provides clear scalar gradient feedback to the Strategy Researcher agent during iterative research loops.

### Negative / Trade-offs:
- Requires calculating the complete test suite (WFA + Monte Carlo) before a final score can be assigned.
- Parameter weighting reflects institutional preferences (capital preservation over raw speculative return).

---

## 4. COMPLIANCE & VERIFICATION
1. Unit tests in `tests/unit/test_scoring.py` must verify that synthetic metric payloads produce exact deterministic CSS floats matching the formula.
2. Ranking queries must sort candidates strictly by `composite_strategy_score DESC`.
3. Integration tests must verify that strategies failing any Tier-1 gate have `composite_strategy_score = NULL` and status `REJECTED`.
