# ADR-011: Research Sessions, AI Memory Schema & SQLite Batch Migration
**Status:** Approved & Frozen  
**Date:** 2026-10-08  
**Author:** Lead Quantitative Systems Engineer  
**Governing Documents:** `PROJECT_CONSTITUTION.md`, `ARCHITECTURE.md`, `ADR-005-database-schema-phase-ownership.md`

---

## 1. CONTEXT & PROBLEM STATEMENT

Under `ADR-005`, the 17-table schema defined four Phase 2 tables:
- `validation_results`
- `walk_forward_results`
- `monte_carlo_results`
- `agent_decisions`

While these tables store the mathematical outputs of backtesting validation, they lack high-level relational grouping for **Autonomous Multi-Agent Research Sessions**:
1. When an operator prompts the system to "Explore Asian session Gold range breakout anomalies", that goal spans multiple agent turns, hypothesis formulations, strategy proposals, and iterative validation runs.
2. Without a parent `research_sessions` table, agent decisions and validation results float unanchored or tied only to individual `strategy_versions`.
3. Without a `research_hypotheses` table, economic hypotheses are trapped inside unstructured JSON logs rather than functioning as queryable, pre-registered scientific assertions.
4. Memory models must strictly support the `IS_RESEARCH_MEMORY` vs `OOS_VALIDATION_MEMORY` quarantine to prevent indirect lookahead bias.
5. In SQLite, altering tables to add foreign keys requires table recreating; migrations must use Alembic batch mode to preserve PostgreSQL/SQLite parity.

---

## 2. DECISION

We formally approve the Phase 2 schema activation and targeted extension:

### A. Phase 2 Table Activation (Per ADR-005)
The four provisioned Phase 2 tables transition from `SCHEMA PROVISIONED (Inactive)` to **`ACTIVE & FUNCTIONAL`**:
- `validation_results`: Stores tier-1 gate checks, gross profit factor, drawdown, and DSR results.
- `walk_forward_results`: Stores fold-by-fold rolling window metrics, WFE, and stitched equity series.
- `monte_carlo_results`: Stores bootstrap distribution percentiles ($95^{\text{th}}$ MaxDD, ruin probability).
- `agent_decisions`: Stores immutable audit records of all LLM agent actions, prompt hashes, token usage, and rationale.

---

### B. Two Targeted Schema Extensions
To support structured research memory, we introduce two new first-class relational entities in Phase 2 via an explicit Alembic migration (`002_phase2_research_tables.py`):

```text
+---------------------+           +------------------------+           +----------------------+
|  research_sessions  | 1       * |  research_hypotheses   | 1       * |  strategy_versions   |
|---------------------|-----------|------------------------|-----------|----------------------|
| id (UUID PK)        |           | id (UUID PK)           |           | id (UUID PK)         |
| title (String)      |           | session_id (FK)        |           | hypothesis_id (FK)   |
| target_instrument   |           | thesis_statement (Text)|           | strategy_id (FK)     |
| status (Enum)       |           | expected_regime (Enum) |           | version_tag (String) |
| token_budget (Int)  |           | search_budget_max (Int)|           | composite_score      |
| total_tokens_used   |           | iterations_spent (Int) |           | status (Enum)        |
| created_at (UTC)    |           | status (Enum)          |           | ...                  |
+---------------------+           +------------------------+           +----------------------+
```

#### 1. `research_sessions`
- **Purpose:** Groups an end-to-end multi-agent research objective.
- **Key Columns:** `id` (UUID PK), `title` (VARCHAR 128), `target_instrument` (VARCHAR 32), `status` (Enum: `ACTIVE`, `COMPLETED`, `ABORTED`), `token_budget` (INTEGER default 100,000), `total_tokens_used` (INTEGER default 0), `created_at`, `updated_at`.
- **Phase Owner:** Phase 2.

#### 2. `research_hypotheses`
- **Purpose:** Captures pre-registered falsifiable economic theses.
- **Key Columns:** `id` (UUID PK), `session_id` (UUID FK -> `research_sessions.id`), `thesis_statement` (TEXT), `market_regime` (VARCHAR 32), `search_budget_max` (INTEGER default 10), `iterations_spent` (INTEGER default 0), `status` (Enum: `ACTIVE`, `VALIDATED`, `FALSIFIED`, `EXHAUSTED`), `created_at`.
- **Phase Owner:** Phase 2.

---

### C. Foreign Key Linkage & SQLite Batch Migration Mandate (P3-2 Resolution)
1. A nullable foreign key `hypothesis_id` (UUID FK -> `research_hypotheses.id`) is added to `strategy_versions`.
2. **Alembic Batch Mode Mandate:** Because SQLite does not support standard `ALTER TABLE ADD CONSTRAINT FOREIGN KEY`, migration `002_phase2_research_tables.py` must strictly use Alembic's batch operations:
   ```python
   with op.batch_alter_table("strategy_versions") as batch_op:
       batch_op.add_column(sa.Column("hypothesis_id", sa.String(36), nullable=True))
       batch_op.create_foreign_key(
           "fk_strategy_versions_hypothesis",
           "research_hypotheses",
           ["hypothesis_id"],
           ["id"],
           ondelete="SET NULL",
       )
   ```
   This guarantees 100% schema parity across SQLite (local development) and PostgreSQL 16 (production).

---

### D. Memory Partition Mapping (P1-3 Alignment)
The schema models explicitly enforce the `IS_RESEARCH_MEMORY` vs `OOS_VALIDATION_MEMORY` quarantine:
- `research_sessions`, `research_hypotheses`, and `strategy_versions` (In-Sample AST and parameters) form the `IS_RESEARCH_MEMORY` domain.
- `walk_forward_results` and `monte_carlo_results` are explicitly tagged as `OOS_VALIDATION_MEMORY`.
- Queries initiated by or on behalf of Agent 3 (Strategy Researcher) are structurally forbidden from joining against OOS tables.

---

## 3. CONSEQUENCES

### Positive:
- **Scientific Auditability:** Researchers can trace every strategy proposal back to its originating economic thesis and overarching research session.
- **Zero Schema Incompatibility:** Batch migration mode prevents SQLite schema corruption while maintaining strict foreign key constraints.
- **Strict Anti-Leakage:** Structural separation of session tables prevents OOS performance metrics from leaking into agent prompt contexts.

### Negative / Trade-offs:
- Adds two new tables that require lifecycle management.

---

## 4. COMPLIANCE & VERIFICATION
1. Alembic migrations must execute and roll back cleanly on both PostgreSQL 16 and SQLite 3 (`pytest tests/unit/test_database.py`).
2. Foreign key cascade behavior must be verified: Deleting a research hypothesis sets `strategy_versions.hypothesis_id = NULL` without deleting the strategy.
