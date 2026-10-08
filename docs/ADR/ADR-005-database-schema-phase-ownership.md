# ADR-005: Database Schema Phase Ownership & Functional Boundaries
**Status:** Approved & Frozen  
**Date:** 2026-10-08  
**Author:** Lead Quantitative Systems Engineer  
**Governing Documents:** `PROJECT_CONSTITUTION.md`, `ARCHITECTURE.md`, `DATABASE.md`

---

## 1. CONTEXT & PROBLEM STATEMENT

The initial Alembic migration (`001_initial_schema.py`) and SQLAlchemy ORM models defined 17 relational tables:
1. `instruments`
2. `market_data_metadata`
3. `strategies`
4. `strategy_versions`
5. `strategy_parameters`
6. `backtests`
7. `backtest_trades`
8. `validation_results`
9. `walk_forward_results`
10. `monte_carlo_results`
11. `signals`
12. `paper_trades`
13. `live_trades`
14. `positions`
15. `risk_events`
16. `market_regimes`
17. `agent_decisions`
18. `system_events`

While designing a unified relational schema upfront provides foreign-key consistency and avoids painful migrations across phases, it introduced an audit discrepancy:
The existence of tables like `live_trades`, `paper_trades`, `validation_results`, and `agent_decisions` led early completion reports to claim or imply that live trading, paper trading, and multi-agent AI validation were "implemented" in Phase 1.

In reality, Phase 1 only exercises the ingestion, backtesting, strategy definition, and system event persistence models. The remaining tables are empty schemas without operational engines behind them.

---

## 2. DECISION

We formally establish the **Database Schema Phase Ownership Invariant**:

### 1. Unified Relational Foundation Recognized
The 17-table relational schema is retained as the unified physical data model across all 4 phases to preserve relational integrity, foreign key constraints, and avoid disruptive schema refactoring during subsequent phases.

### 2. Strict Phase Ownership Assignment
Every database model is explicitly tagged with its owning lifecycle phase:

| Phase | Phase Name | Owned Tables | Operational Status in Phase 1 |
| :--- | :--- | :--- | :--- |
| **Phase 1** | Foundation & Backtest | `instruments`, `market_data_metadata`, `strategies`, `strategy_versions`, `strategy_parameters`, `backtests`, `backtest_trades`, `market_regimes`, `system_events` | **ACTIVE & FUNCTIONAL** |
| **Phase 2** | AI & Validation | `validation_results`, `walk_forward_results`, `monte_carlo_results`, `agent_decisions` | **SCHEMA PROVISIONED (Inactive)** |
| **Phase 3** | Paper Trading & Risk | `signals`, `paper_trades`, `positions`, `risk_events` | **SCHEMA PROVISIONED (Inactive)** |
| **Phase 4** | Live Execution | `live_trades` | **SCHEMA PROVISIONED (Inactive)** |

### 3. Documentation & Verification Rule
- Under no circumstances may documentation, release notes, or completion reports cite the existence of a database table as evidence of a functioning software capability.
- Inactive tables must remain unpopulated in Phase 1 test runs.
- APIs and repositories must return explicit status indicators (e.g., HTTP 501 / Phase Not Activated) if queries are attempted against future phase tables prior to their respective phase implementation.

---

## 3. CONSEQUENCES

### Positive:
- **Zero Schema Churn:** Subsequent phases do not need to rewrite foreign key constraints or restructure database tables.
- **Accurate Auditability:** External and internal audits can clearly verify which tables are active versus which are forward-looking architectural placeholders.
- **Transparent Progress:** Prevents false claims of phase completion.

### Negative / Trade-offs:
- Migrations create tables before their corresponding backend services are built.
- Requires documentation overhead to maintain clear phase distinctions in `DATABASE.md`.

---

## 4. COMPLIANCE & VERIFICATION

1. In `DATABASE.md`, every table description must be explicitly annotated with its `Phase Owner` and `Operational Status`.
2. Phase 1 integration tests must assert that all 9 Phase 1 active tables can be written to and queried.
3. Phase 1 completion reports must never claim functional readiness for Phase 2, 3, or 4 tables.

