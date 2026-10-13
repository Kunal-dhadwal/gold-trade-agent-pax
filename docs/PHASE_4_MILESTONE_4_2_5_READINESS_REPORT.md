# AGentTrXPAUS — SUB-MILESTONE 4.2.5 READINESS REPORT

**Sub-Milestone:** 4.2.5 — Twelve-Gate Deterministic Live Risk Engine & Decoupled `LiveKillSwitchFSM`  
**Target Architecture:** Phase 4 Live Trading Architecture  
**Execution Timestamp:** 2026-10-12T17:00:00+05:30  
**Git Branch / Commit:** `release1.1v` (HEAD at `054cd00d4219e804c2b1ec5859c65a81e1892108`)  
**Readiness Status:** **READY FOR IMPLEMENTATION AUTHORIZATION**

---

## 1. Executive Summary

Sub-Milestone 4.2.4 has successfully completed implementation and passed independent safety audit certification ([`docs/GATE_AUDIT_4_2_4_REPORT.md`](file:///home/epps/MyProject/AGentTrXPAUS/docs/GATE_AUDIT_4_2_4_REPORT.md), Verdict: **PASS**). 

This Readiness Report establishes the formal architectural blueprint, exact approved scope, dependency graph, risk controls, and test verification plan for **Sub-Milestone 4.2.5: Twelve-Gate Deterministic Live Risk Engine & Decoupled `LiveKillSwitchFSM`**, prior to writing implementation code.

In strict compliance with the [`PROJECT_CONSTITUTION.md`](file:///home/epps/MyProject/AGentTrXPAUS/PROJECT_CONSTITUTION.md), no implementation code for Sub-Milestone 4.2.5 has been written during this review.

---

## 2. Upstream Milestone Baseline Status (4.2.1–4.2.4)

| Milestone | Deliverables | Verification Evidence | Audit Status |
|---|---|---|---|
| **Sub-Milestone 4.2.1** | PostgreSQL relational schemas, Alembic migrations, `fee_manager.py` with negative maker rebates | `test_fee_manager.py`, `test_database.py` | **PASS** ([`GATE_AUDIT_4_2_1_REPORT.md`](file:///home/epps/MyProject/AGentTrXPAUS/docs/GATE_AUDIT_4_2_1_REPORT.md)) |
| **Sub-Milestone 4.2.2** | 11-state order lifecycle FSM (`fsm.py`), idempotent router (`order_router.py`), dynamic bracket scaling, synthetic OCO cancellation | `test_fsm.py`, `test_order_router.py`, `test_idempotency.py` | **PASS** ([`GATE_REAUDIT_4_2_2_REPORT.md`](file:///home/epps/MyProject/AGentTrXPAUS/docs/GATE_REAUDIT_4_2_2_REPORT.md)) |
| **Sub-Milestone 4.2.3** | PostgreSQL distributed leadership leases (`leadership.py`), fencing tokens, 3-way continuous reconciliation daemon (`reconciliation.py`), zero automated liquidation | `test_leadership.py`, `test_reconciliation.py`, `test_reconciliation_integration.py` | **PASS** ([`GATE_AUDIT_4_2_3_REPORT.md`](file:///home/epps/MyProject/AGentTrXPAUS/docs/GATE_AUDIT_4_2_3_REPORT.md)) |
| **Sub-Milestone 4.2.4** | Authenticated operator API (`routes/reconciliation.py`), JWT ADMIN RBAC, AI lockout, HMAC-SHA256, durable nonce replay protection, fail-fast flatten, funding tolerances | `test_operator_endpoints.py`, `test_reconciliation_adversarial.py`, `test_reconciliation_tolerances.py` | **PASS** ([`GATE_AUDIT_4_2_4_REPORT.md`](file:///home/epps/MyProject/AGentTrXPAUS/docs/GATE_AUDIT_4_2_4_REPORT.md)) |

---

## 3. Exact Approved Scope for Sub-Milestone 4.2.5

Governed strictly by Section 3.5 of the Implementation Plan and Sections F.1–F.3, G.1, G.2 of the Design Specification:

### 3.1 Primary Deliverables

1. **`src/risk/live_risk_engine.py` (New Module):**
   - Implements the twelve (12) deterministic mathematical live risk gates with unconditional veto authority.
   - Enforces pre-flight bootstrap assertion: `LIVE_TRADING_ENABLED == True` required for order dispatch.
   - Enforces separation of controls: Entry controls vs Protective Exit bypass.
   - Implements dollar-risk sizing clamping ($Q_{\text{max\_risk}} = \frac{\text{Equity} \times 0.010}{|P_{\text{entry}} - P_{\text{SL}}| \times \text{ContractValue}}$) clamped to lot boundaries.
   - Integrates with [`HighWaterMarkManager`](file:///home/epps/MyProject/AGentTrXPAUS/src/risk/high_water_mark.py) to measure Gate 1 drawdown against persisted peak equity in `account_equity_high_water_mark`.
   - Implements Gate 12 burst rejection circuit breaker ($\ge 3$ rejections in 10 minutes or quantity drift $\ne 0$).

2. **`src/risk/live_kill_switch.py` (New Module):**
   - Decoupled `LiveKillSwitchFSM` implementing the institutional multi-stage lifecycle:
     $$\text{NORMAL} \longrightarrow \text{TRIGGERED} \longrightarrow \text{NEW\_TRADING\_BLOCKED} \longrightarrow \begin{cases} \text{MANUAL\_RESET\_REQUIRED} & \text{(Ghost exposure, zero auto-liquidation)} \\ \text{FLATTENING} \longrightarrow \text{FLATTENED} & \text{(Gate 1 Drawdown } \ge 6.0\% \text{ / Operator)} \end{cases}$$
   - Strict AI principal lockout: zero reset authority.
   - Preserves frozen Phase 1–3 files ([`src/risk/kill_switch.py`](file:///home/epps/MyProject/AGentTrXPAUS/src/risk/kill_switch.py), [`src/risk/paper_risk_engine.py`](file:///home/epps/MyProject/AGentTrXPAUS/src/risk/paper_risk_engine.py)) 100% untouched.

3. **`src/api/routes/risk.py` (Enhancement):**
   - Exposes live kill switch endpoints and telemetry with discrepancy gating.

---

## 4. Technical Specifications: The 12 Deterministic Live Risk Gates

| Gate ID | Risk Gate Name | Enforcement Condition | Action on Breach | Gate Type | Protective Exit Exemption? |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Gate 1** | Portfolio Peak-to-Trough Drawdown Cap | $\text{DrawdownPct} \ge 6.0\%$ from high-water mark | Unconditional Kill Switch Trigger $\to$ `FLATTENING` (liquidate recognized positions) | **RUNTIME SAFETY** | **MANDATORY (Never Bypassed)** |
| **Gate 2** | Daily Max Loss Limit | Daily loss $\ge 3.0\%$ from 00:00 UTC equity | Halts all new entries for remainder of UTC day | **ENTRY GATE** | **BYPASSED** (allows closing trades) |
| **Gate 3** | Risk Per Trade & Sizing Clamping | $\text{DollarRisk} \le 1.0\% \times \text{Equity}$ | Automatically clamps quantity to $Q_{\text{max\_risk}}$; rejects if below `min_order_size` | **ENTRY GATE** | **BYPASSED** (exits match position size) |
| **Gate 4** | Leverage & Notional Cap | $\text{Notional} \le 5.0\times \text{Equity}$ & $\le \text{MaxNotional}$ | Clamps quantity to leverage ceiling; rejects if below minimum | **ENTRY GATE** | **BYPASSED** (exits reduce exposure) |
| **Gate 5** | Single Strategy Lock | Active positions $\ge 1$ | Rejects new entry signals; enforces 1 active position per account | **ENTRY GATE** | **BYPASSED** (exits close active position) |
| **Gate 6** | Consecutive Loss Streak Guard | Consecutive losses $\ge 3$ trades | Enforces mandatory 2-hour strategy cooldown | **ENTRY GATE** | **BYPASSED** (cooldown applies to entries) |
| **Gate 7** | Market Regime Filter | Current market regime $\notin$ strategy whitelist | Rejects entry signal; preserves flat position | **ENTRY GATE** | **BYPASSED** (protective stops must exit) |
| **Gate 8** | Feed Latency & Stale Bar | Mark / ticker feed delay $> 5.0\text{s}$ | Blocks new entries; flags market data connectivity warning | **ENTRY GATE** | **BYPASSED** for emergency flatten |
| **Gate 9** | Server Clock Drift SLA | Clock drift $> 1000\text{ms}$ relative to Delta | Halts new entries until NTP synchronization restored | **ENTRY GATE** | **MANDATORY** (Emergency flatten follows Section 3.2.5) |
| **Gate 10** | Spread & Slippage Ceiling | Spread $> \$1.50$ OR $> 2.0\times \text{Median}_{20}$ | Rejects entries during volatile liquidity dry-ups | **ENTRY GATE** | **BYPASSED** (protective stops must trigger) |
| **Gate 11** | Fee & Metadata Completeness | Tier 1/2 fees unverified or stale | Fail-closed for new entries | **ENTRY GATE** | **BYPASSED** (stale fee cache cannot trap positions) |
| **Gate 12** | Desynchronization & Burst Rejection | Quantity drift $\ne 0$ OR $\ge 3$ rejections in 10m | Unconditional Kill Switch Trigger; blocks all trading | **RUNTIME SAFETY** | **MANDATORY (Never Bypassed)** |

### 4.1 Gate 1 Mathematical Specification
$$\text{DrawdownPct} = \frac{\text{PeakEquity} - \text{CurrentEquity}}{\text{PeakEquity}} \times 100\%$$
- **Threshold Policy:** Exactly **6.0%** (`settings.MAX_PORTFOLIO_DRAWDOWN_PCT = 6.0`).
- **Enforcement Action:** If $\text{DrawdownPct} \ge 6.0\%$, unconditionally trips `LiveKillSwitchFSM` into `TRIGGERED` $\to$ `NEW_TRADING_BLOCKED` $\to$ `FLATTENING`. Dispatches authenticated `reduce_only: true` market orders to liquidate recognized positions.
- **Persistence Authority:** Measured strictly against `AccountEquityHighWaterMark` in PostgreSQL table `account_equity_high_water_mark`.

### 4.2 Gate 3 Sizing Clamping Formula
$$Q_{\text{max\_risk}} = \frac{\text{Equity} \times 0.010}{|P_{\text{entry}} - P_{\text{SL}}| \times \text{ContractValue}}$$
$$Q_{\text{clamped}} = \min(Q_{\text{requested}}, Q_{\text{max\_risk}})$$
- Clamped quantity is rounded down to integer contract lot boundaries. If $Q_{\text{clamped}} < \text{min\_order\_size}$, the order is rejected.

---

## 5. Technical Specifications: Decoupled `LiveKillSwitchFSM`

### 5.1 Formal State Machine & Transitions

```mermaid
stateDiagram-v2
    [*] --> NORMAL
    NORMAL --> TRIGGERED: Gate 1 Breach / Burst Rejection / Drift / Ghost / Operator
    TRIGGERED --> NEW_TRADING_BLOCKED: All Pending Entry Orders Cancelled

    NEW_TRADING_BLOCKED --> MANUAL_RESET_REQUIRED: Ghost Exposure (Stops Preserved, Zero Auto-Liquidation)
    NEW_TRADING_BLOCKED --> FLATTENING: Gate 1 Drawdown (>= 6.0%) / Operator Emergency
    FLATTENING --> FLATTENED: Recognized Positions Closed via Reduce-Only
    FLATTENED --> MANUAL_RESET_REQUIRED: All Recognized Positions Confirmed Flat

    MANUAL_RESET_REQUIRED --> NORMAL: Operator HMAC Signature AND Zero Discrepancies
```

### 5.2 Critical Invariants
1. **AI Principal Denial:** AI agents, LLM providers, and automated bots possess ZERO reset authority. Any reset request from an AI identity is rejected with `HTTP 403 Forbidden` (`SECURITY VETO`).
2. **Ghost Position Handling:** Transitions to `MANUAL_RESET_REQUIRED`. Automated market liquidation is strictly forbidden. Resting protective stops remain active.
3. **Recognized Drawdown Flattening:** Only Gate 1 breach ($\ge 6.0\%$) or explicit operator command transitions to `FLATTENING`, issuing authenticated `reduce_only: true` market orders to close recognized positions.
4. **Pre-Reset Discrepancy Gate:** Reset requires $\text{DiscrepancyCount} == 0$ and an immediate live three-way reconciliation cycle.

---

## 6. Investigation & Execution Plan for Skipped PostgreSQL Tests

### 6.1 Status of the Two Skipped Tests
In `tests/integration/database/test_postgresql_runtime_compatibility.py`:
- `test_postgresql_16_runtime_and_schema_compatibility` (Line 178)
- `test_postgresql_schema_rollback_safety_preconditions` (Line 224)
These tests probe `postgresql+psycopg2://postgres:postgrespassword@127.0.0.1:5432/gold_trading_test`. They are currently skipped because the PostgreSQL 16 daemon on the host is offline (`127.0.0.1:5432 - no response`).

### 6.2 Execution Procedure against Disposable Database
When PostgreSQL 16 is started:
1. Ensure the PostgreSQL 16 daemon is running: `pg_isready -h 127.0.0.1 -p 5432`.
2. Confirm the test database exists: `createdb -h 127.0.0.1 -U postgres gold_trading_test`.
3. Run the targeted compatibility suite:
   ```bash
   ./.venv/bin/pytest tests/integration/database/test_postgresql_runtime_compatibility.py -v -rs
   ```
4. Verify that `validate_test_database_target()` guarantees all Alembic migrations and rollback operations target `gold_trading_test` exclusively and never touch `gold_trading`.

---

## 7. Test Plan for Sub-Milestone 4.2.5

### 7.1 New Test Modules
1. `tests/unit/risk/test_live_risk_engine.py`:
   - Verification of all 12 deterministic gates.
   - Gate 1 drawdown veto ($\ge 6.0\%$) and high-water mark tracking.
   - Gate 2 daily loss limit ($\ge 3.0\%$) and reset at 00:00 UTC.
   - Gate 3 sizing clamping down to contract lot boundaries.
   - Gate 4 leverage and notional cap ($5.0\times$ equity).
   - Gate 5 single strategy position lock.
   - Gate 6 consecutive loss streak (3 trades $\to$ 2h cooldown).
   - Gate 7 market regime whitelist filter.
   - Gate 8 stale bar and feed latency ($> 5.0\text{s}$).
   - Gate 9 clock drift ($> 1000\text{ms}$).
   - Gate 10 spread ceiling ($> \$1.50$ or $> 2.0\times$ median).
   - Gate 11 fee verification fail-closed.
   - Gate 12 burst rejection circuit breaker ($\ge 3$ rejections in 10m).
   - Protective exit exemptions (proving SL/TP bypass Gates 2, 5, 6, 7, 11).
   - Bootstrap assertion: `LIVE_TRADING_ENABLED == True` required.
2. `tests/unit/risk/test_live_kill_switch.py`:
   - Verification of all 7 states and valid transitions.
   - Invalid transition rejection (`InvalidStateTransitionError`).
   - Ghost exposure transition to `MANUAL_RESET_REQUIRED` with zero auto-liquidation.
   - Gate 1 drawdown transition to `FLATTENING`.
   - Strict AI principal rejection on reset.

### 7.2 Regression Suites
- Existing Phase 3 risk tests: `tests/unit/risk/test_kill_switch_and_risk_engine.py` (Must remain 100% passing).
- High-water mark tests: `tests/unit/risk/test_high_water_mark.py` (Must remain 100% passing).
- Full repository regression suite: All 353 passed tests must remain regression-free.

---

## 8. Constitutional Guardrails & Invariants

- **`LIVE_TRADING_ENABLED = False`:** Hard-locked in `src/core/config.py` line 56.
- **Decision A Neutrality:** Decision A remains strictly **OPEN**. Zero outbound network traffic to Delta Testnet or live production.
- **Frozen Baseline Integrity:** Zero modifications permitted to Phase 1–3 frozen core files:
  - `src/core/config.py`
  - `src/risk/kill_switch.py`
  - `src/risk/paper_risk_engine.py`
- **Git Repository Cleanliness:** Zero commits, tags, pushes, or resets permitted. Working tree changes preserved.
- **Non-Destructive Database Operations:** No database deletions or Docker volume removals.

---

## 9. Readiness Checklist

- [x] Phase 4.2 Implementation Plan Section 3.5 inspected and mapped.
- [x] Phase 4.2 Design Specification Sections F.1–F.3 inspected and mapped.
- [x] High-water mark manager (`src/risk/high_water_mark.py`) verified in upstream baseline.
- [x] Reconciliation discrepancy gate (`src/api/routes/risk.py`) verified in upstream baseline.
- [x] Frozen core file boundaries confirmed untouched.
- [x] Skipped PostgreSQL test procedure documented.
- [x] 12 live risk gates specified mathematically.
- [x] `LiveKillSwitchFSM` state machine and transitions specified.
- [x] Test plan defined with coverage targets.
- [x] Full regression suite verified at 353 passed, 2 skipped, 1 warning (Exit code: 0).

**Sub-Milestone 4.2.5 is fully planned and ready for user implementation authorization.**

