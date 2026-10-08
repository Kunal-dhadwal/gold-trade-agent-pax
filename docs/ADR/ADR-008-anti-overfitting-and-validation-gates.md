# ADR-008: Anti-Overfitting Architecture, OOS Quarantine & Configurable Validation Gates
**Status:** Approved & Frozen  
**Date:** 2026-10-08  
**Author:** Lead Quantitative Systems Engineer  
**Governing Documents:** `PROJECT_CONSTITUTION.md`, `ARCHITECTURE.md`, `ADR-004-ai-vs-deterministic-boundaries.md`

---

## 1. CONTEXT & PROBLEM STATEMENT

Automated AI strategy generation introduces severe risks of **Data Snooping, Selection Bias, and Overfitting**:
- An automated agent can generate hundreds of candidate parameter permutations until it encounters an anomaly that yielded high historical returns purely by chance.
- Standard In-Sample (IS) backtests systematically overestimate live performance.
- If an AI agent has access to Out-of-Sample (OOS) performance history, the holdout window becomes indirect training data, completely invalidating the statistical independence of OOS validation.
- Hardcoding validation thresholds as immutable mathematical truths prevents institutional calibration across different time horizons (e.g. 1m scalping vs 4h swing trading).

A deterministic, multi-stage gatekeeper and strict memory quarantine system is required to ruthlessly eliminate overfit strategies while protecting OOS holdout integrity.

---

## 2. DECISION

We formally establish the **Anti-Overfitting, Memory Quarantine & Validation Policy Architecture**:

### A. Pre-Backtest Hypothesis Pre-Registration
Before any backtest may be executed, the Strategy Researcher must formulate and record an economic hypothesis in `research_hypotheses`:
- **Economic Rationale:** Why should this market inefficiency exist in Gold? (e.g., London fix liquidity imbalance, Asian session range contraction, breakout from European open).
- **Falsifiable Expectation:** Expected holding duration, favorable regime, and risk profile.
- **Search Budget Cap:** A hypothesis has a strict quota of **maximum 10 candidate iterations**. If no valid strategy passes within 10 proposals, the hypothesis is declared **FALSIFIED** and permanently closed. This prevents endless curve-fitting on a single premise.

---

### B. Out-of-Sample (OOS) Memory Quarantine (P1-3 Resolution)

To guarantee that Out-of-Sample data never functions as indirect training data for LLM prompts, research memory is partitioned into two mutually exclusive visibility classes:

```text
+---------------------------------------------------------------------------------------------------+
| TWO-TIER RESEARCH MEMORY VISIBILITY PARTITIONS                                                    |
+-------------------------------------------------+-------------------------------------------------+
| 1. IS_RESEARCH_MEMORY (Open to AI Ideation)     | 2. OOS_VALIDATION_MEMORY (Strictly Quarantined) |
+-------------------------------------------------+-------------------------------------------------+
| - Economic hypotheses & theses                  | - OOS Performance Metrics (Sharpe, GPF, Sortino) |
| - Strategy proposal ASTs                        | - OOS Equity curves & drawdowns                 |
| - In-Sample metrics (IS_Sharpe, IS_GPF, IS_DD)  | - OOS Trade-level logs & execution timestamps   |
| - In-Sample diagnostic feedback & gate failures | - Walk-Forward fold-by-fold OOS metrics         |
| - Parameter search ranges & historical plateaus | - Monte Carlo simulated failure paths           |
| - Statistical features derived ONLY from IS data| - Out-of-Sample regime breakdown                |
+-------------------------------------------------+-------------------------------------------------+
```

#### 1. Agent Access Control Matrix:
| Component / Agent | `IS_RESEARCH_MEMORY` Access | `OOS_VALIDATION_MEMORY` Access |
| :--- | :---: | :---: |
| **Agent 1: Market Analyst** | **ALLOWED** | **STRICTLY BLOCKED** |
| **Agent 2: Regime Analyst** | **ALLOWED** | **STRICTLY BLOCKED** |
| **Agent 3: Strategy Researcher** | **ALLOWED** | **STRICTLY BLOCKED** |
| **Agent 4: Quant Validator** | **ALLOWED** | **ALLOWED** (Validation Only) |
| **Agent 5: Performance Analyst** | **ALLOWED** | **ALLOWED** (Reporting Only) |
| **Public Strategy Leaderboard** | **ALLOWED** | **ALLOWED** (High-level scores only) |

#### 2. Typed Context Isolation:
Developers are prevented from accidentally passing generic database memory to the Strategy Researcher. Context injection must strictly use the typed `StrategyResearchContext`:

```python
class StrategyResearchContext(BaseModel):
    session_id: UUID4
    hypothesis_id: UUID4
    thesis_statement: str
    target_regime: RegimeEnum
    market_brief: MarketBrief
    is_diagnostic_history: List[ISDiagnosticRecord]
    parameter_plateaus: List[ParameterPlateauRecord]
    # OOS metrics, equity curves, and trade records are STRUCTURALLY ABSENT from this model!
```

#### 3. Database Query & API Filtering Rules:
- All queries executed on behalf of Agent 3 must include the explicit SQL filter: `WHERE partition = 'IN_SAMPLE'`.
- The API endpoint `/api/v1/research/sessions/{id}/generate-strategy` serializes only `StrategyResearchContext`. Any attempt to serialize OOS tables into strategy generation prompts raises a `DataQuarantineViolationError`.
- Post-research retrospective analysis by human operators can inspect both partitions, but agent generation prompts remain permanently air-gapped from OOS memory.

---

### C. Configurable Validation Policy Thresholds (P2-1 Resolution)

Validation gates are categorized by their nature. Thresholds are maintained as configurable variables in `src/core/config.py` with institutional default values:

```text
+---+------------------------------+---------+-----------------------+---------------------------------------+
| # | Policy Parameter             | Default | Nature Classification | Economic / Statistical Rationale      |
+---+------------------------------+---------+-----------------------+---------------------------------------+
| 1 | MIN_TRADES_GATE              | 50      | Research Heuristic    | Sample size power for central limit   |
| 2 | MIN_EXPECTANCY_SPREAD_RATIO  | 0.15    | Research Heuristic    | Guarantees edge exceeds gold friction |
| 3 | MIN_GROSS_PROFIT_FACTOR      | 1.30    | Research Heuristic    | Eliminates marginal / breakeven sets  |
| 4 | MAX_DRAWDOWN_GATE            | 0.15    | Risk Invariant        | Bounds peak-to-trough capital loss    |
| 5 | MAX_CONSECUTIVE_LOSSES       | 8       | Research Heuristic    | Protects against adverse loss streaks |
| 6 | MIN_OOS_RETENTION_RATIO      | 0.65    | Research Heuristic    | Disqualifies curve-fitted decay       |
| 7 | MAX_RUIN_PROBABILITY         | 0.05    | Risk Invariant        | Max acceptable probability of 20% DD  |
| 8 | MIN_WFE_RATIO                | 0.50    | Research Heuristic    | Walk-forward consistency standard     |
| 9 | MIN_STRESS_PROFIT_FACTOR     | 1.10    | Risk Invariant        | Margin of safety under +50% friction  |
| 10| MAX_PARAM_DEGRADATION        | 0.40    | Research Heuristic    | Disqualifies knife-edge spikes        |
| 11| CSS_TIER1_THRESHOLD          | 80.0    | Configurable Policy   | Prime candidate Phase 3 eligibility   |
| 12| CSS_TIER2_THRESHOLD          | 70.0    | Configurable Policy   | Reserve candidate threshold           |
+---+------------------------------+---------+-----------------------+---------------------------------------+
```

#### Authority Rule on Validation Policies:
- **Human Operator Exclusive:** Validation policies can only be adjusted by human operators via environment variables or configuration files.
- **AI Modification Forbidden:** AI agents are **STRICTLY FORBIDDEN** from modifying, relaxing, or overriding validation policies.
- Strategy proposals cannot include fields that alter gates. Any strategy attempting to set or override gate thresholds is rejected at schema validation.

---

### D. Cascading Early-Exit Pipeline (P2-3 Resolution)

To eliminate unnecessary computational expenditure and prevent invalid strategies from being evaluated, validation executes as a strict cascading early-exit pipeline:

```text
Step 1: AST Schema Validation (Instant, 0 CPU)
   ├── FAIL ──► ABORT & Request 1 Correction Attempt
   └── PASS
        ▼
Step 2: In-Sample (IS) Deterministic Backtest (~100ms)
        ▼
Step 3: In-Sample Hard Gate Evaluation (~1ms)
   ├── Checks: Trade Count >= 50, Expectancy >= 0.15*Spread, GPF >= 1.30, MaxDD <= 15%, Streak <= 8
   ├── FAIL ──► REJECT IMMEDIATELY. DO NOT RUN WFA. DO NOT RUN MONTE CARLO.
   └── PASS
        ▼
Step 4: Walk-Forward Analysis (WFA) (1-3s)
   ├── Checks: WFE >= 0.50, GPF_OOS / GPF_IS >= 0.65
   ├── FAIL ──► REJECT IMMEDIATELY. DO NOT RUN MONTE CARLO.
   └── PASS
        ▼
Step 5: Monte Carlo Stress Testing (2-5s)
   ├── 2,000 bootstrap order reshuffles + friction stress (+50% spread/slippage)
   ├── Checks: Ruin Prob < 5%, Stress GPF >= 1.10, Param Degradation <= 40%
   ├── FAIL ──► REJECT.
   └── PASS
        ▼
Step 6: Composite Strategy Score (CSS) Calculation
   └── Assigns final scalar score [0.0, 100.0] and updates Strategy Leaderboard.
```

**Pipeline Rule:** Monte Carlo and CSS are **NEVER** executed for a strategy that failed an earlier gate. CSS cannot resurrect or rescue a strategy that failed any mandatory hard gate.

---

## 3. CONSEQUENCES

### Positive:
- **Zero OOS Leakage:** Quarantining OOS metrics preserves the true statistical validity of forward testing.
- **Efficient Compute Utilization:** The early-exit pipeline drops 90%+ of non-viable strategies in $< 150\text{ ms}$, saving compute for genuinely promising candidates.
- **Operational Flexibility:** System operators can tune heuristic thresholds via `.env` without modifying engine source code.

### Negative / Trade-offs:
- Adding memory visibility partitions requires strict query builder filters in the database repository layer.

---

## 4. COMPLIANCE & VERIFICATION
1. Integration tests must verify that `StrategyResearchContext` contains zero references or values from OOS tables.
2. Unit tests must inject strategies that fail In-Sample GPF (< 1.30) and assert that `WFAEngine` and `MonteCarloEngine` are never invoked.
3. Automated tests must verify that altering gate policies requires configuration privileges and cannot be executed via strategy proposal payloads.
