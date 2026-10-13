# AGentTrXPAUS — GATE-AUDIT-4.2.4 INDEPENDENT SAFETY AUDIT REPORT

**Sub-Milestone:** 4.2.4 — Three-Way Continuous Reconciliation Daemon Enhancements & Authenticated Operator Recovery Endpoints  
**Document:** `docs/GATE_AUDIT_4_2_4_REPORT.md`  
**Audit Date:** 2026-10-12  
**Target Architecture:** Distributed Three-Way Reconciliation & Authenticated Recovery Stack  
**Target Environment:** Local Offline Multi-Worker Emulation & Isolated PostgreSQL / SQLite Tests  
**Audit Baseline Git Commit:** `054cd00d4219e804c2b1ec5859c65a81e1892108` (`v1.3.0-phase3-verified`)  
**Safety Invariant:** `LIVE_TRADING_ENABLED: bool = False` (`src/core/config.py` line 56)  
**Ratified Risk Policy:** Gate 1 Peak-to-Trough Drawdown Cap = **6.0%** (`MAX_PORTFOLIO_DRAWDOWN_PCT = 6.0`)  
**Decision A Status:** **STRICTLY OPEN** (Zero Delta Testnet traffic, zero external network sockets)  
**Final Audit Verdict:** **PASS**

---

## 1. Executive Summary & Audit Scope

An exhaustive, independent, evidence-based safety audit of Sub-Milestone 4.2.4 was conducted. Sub-Milestone 4.2.4 delivers:

1. **Authenticated Operator API Router (`src/api/routes/reconciliation.py`):**
   - Mounted under `settings.API_PREFIX` (`/api/v1/reconciliation`) in `src/api/main.py`.
   - `POST /adopt-ghost-position`: Synchronizes exchange position into local database [`Position`](file:///home/epps/MyProject/AGentTrXPAUS/src/database/models/trading.py), writes an immutable audit record in [`OperatorReconciliationAuditLog`](file:///home/epps/MyProject/AGentTrXPAUS/src/database/models/live.py), clears in-memory ghost position alarms, and triggers a clean reconciliation pass.
   - `POST /operator-flatten`: Dispatches an authenticated emergency `reduce_only` market order, fail-fast protected against clock drift and transport outages.
   - `GET /status`: Telemetry endpoint querying real-time discrepancy counts, ghost positions, and leadership lease status.
   - `GET /snapshots`: Paginated query endpoint for historical audit snapshots.

2. **Constitutional Authorization & Replay Prevention Guardrails:**
   - **RFC 7519 JWT RBAC:** Strict enforcement of `ADMIN` role for mutation endpoints (`/adopt-ghost-position`, `/operator-flatten`) and `VIEWER` role for read-only queries (`/status`, `/snapshots`).
   - **Constitutional AI Lockout:** `HTTP 403 Forbidden` (`SECURITY VETO`) returned whenever token subject or payload `operator_id` matches AI prefixes (`ai_*`, `bot_*`, `agent_*`, `llm_*`).
   - **Canonical HMAC-SHA256 Signatures:** Cryptographic verification over structured canonical payload strings prevents payload tampering and transit alteration.
   - **Durable 24-Hour Nonce Consumption:** Replay protection enforced via atomic database checks and insertions into [`ConsumedAuthorizationNonce`](file:///home/epps/MyProject/AGentTrXPAUS/src/database/models/live.py). Duplicate nonces return `HTTP 409 Conflict`.
   - **Durable 24-Hour Idempotency Keys:** Duplicate requests bearing identical `idempotency_key` tokens are rejected with `HTTP 409 Conflict`.

3. **Emergency Flatten Fail-Fast Guard:**
   - `operator_flatten` evaluates local clock drift against the exchange. If clock drift exceeds 1000ms and the REST probe fails, or if an exchange transport outage is detected, the endpoint immediately fails fast with `HTTP 503 Service Unavailable`, preventing blind or out-of-order execution.

4. **Kill Switch Reset Discrepancy Gate (`src/api/routes/risk.py`):**
   - `POST /api/v1/risk/kill-switch/reset` integrates an authoritative discrepancy gate: triggers an immediate three-way reconciliation pass if daemon is registered, and returns `HTTP 409 Conflict` if in-memory `discrepancy_count > 0` or if the latest database snapshot records unresolved discrepancies or is stale ($> 60\text{s}$).

5. **Dynamic Tolerances & Continuous Reconciliation Enhancements (`src/execution/reconciliation.py`):**
   - **Funding Settlement Window:** Evaluates exact second-level boundaries ($\pm 15\text{m}$, 900 seconds) around 00:00, 08:00, 16:00 UTC.
   - **Dynamic Cash Tolerance:** Expands during funding windows ($\text{Tol}_{\text{cash}} = \text{Tol}_{\text{base}} + \text{Tol}_{\text{funding}}$) to absorb in-flight settlement accruals; records settlements into `live_funding_events`.
   - **Basis-Aware UPnL Tolerance:** Evaluates mark-to-market valuations using:
     $$\text{Tol}_{\text{UPnL}} = \text{PositionSize} \times \text{ContractValue} \times \max(\text{Spread}, 3.0 \times \text{BasisTWAP})$$
     Gated to evaluate only when position sizes match in both systems, preventing false-positive double-counting during ghost position events.
   - **Orphaned Synthetic OCO Companion Sweep:** Detects when net position is zero and automatically cancels orphaned resting stop-loss or take-profit companion orders on the exchange and in the database, updating router active exits.

### Audit Verdict: **PASS**
All requirements defined in the Phase 4 Milestone 4.2 Implementation Plan (Section 3.4) and Design Spec (Sections C.3, C.4, G.1, G.2) have been verified, stress-tested under adversarial fault injection, and confirmed with fresh regression test runs.

---

## 2. Remediated Safety Findings (Audit Discovered & Resolved)

During the independent safety audit, 5 concrete safety defects were identified, analyzed, remediated in code, and verified with dedicated unit/adversarial tests:

| Finding ID | Severity | Defect Classification | Root Cause & Failure Scenario | Remediated Implementation & Test Evidence | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **FIND-4.2.4-01** | **HIGH** | Concurrent Replay Race Condition Vulnerability | `_consume_nonce_durable` performed `await db.flush()` without an immediate commit. Under concurrent requests with identical nonces, multiple requests could pass the `select` query before either committed. While database primary key constraints raised `IntegrityError`, this was unhandled, causing FastAPI to return `HTTP 500 Internal Server Error` instead of the required `HTTP 409 Conflict`. | Remediated in `src/api/routes/reconciliation.py`: Added explicit `await db.commit()` inside `_consume_nonce_durable`, wrapped in `try/except IntegrityError` that rolls back and raises `HTTP 409 Conflict`. Verified by adversarial test `test_concurrent_replay_race_protection` (5 concurrent requests $\to$ exactly 1 HTTP 200, 4 HTTP 409, zero HTTP 500). | **RESOLVED** |
| **FIND-4.2.4-02** | **MEDIUM** | Unchecked Idempotency Key Parameter | `AdoptGhostPositionIn` and `OperatorFlattenIn` accepted an optional `idempotency_key` field, but `_consume_nonce_durable` only consumed `nonce`. If a client retried a request using a new nonce but the same idempotency key, duplicate execution occurred. | Remediated in `src/api/routes/reconciliation.py`: Added idempotency key tracking to `_consume_nonce_durable` by checking and storing `IDEMP:<idempotency_key>` in `consumed_authorization_nonces`. Verified by `test_idempotency_key_duplicate_prevention`. | **RESOLVED** |
| **FIND-4.2.4-03** | **HIGH** | Kill Switch Reset Allowed on Stale Snapshots or Without Pre-Reset Cycle | `reset_kill_switch` checked database snapshot discrepancy count, but did not enforce snapshot freshness or trigger an immediate live reconciliation pass. If the daemon crashed hours earlier, an operator could reset the kill switch based on a stale snapshot without verifying current exchange reality. | Remediated in `src/api/routes/risk.py`: If daemon is registered, `reset_kill_switch` triggers an immediate live `await daemon.run_reconciliation_cycle()`. If daemon is offline, snapshot age is checked: if $> 60.0\text{s}$ old, reset is blocked with `HTTP 409 Conflict`. Verified by `test_kill_switch_reset_immediate_cycle_catches_unexplained_position` and `test_kill_switch_reset_blocked_on_stale_snapshot`. | **RESOLVED** |
| **FIND-4.2.4-04** | **LOW** | Minute-Level Funding Window Imprecision | `is_in_funding_settlement_window` evaluated `dt.hour * 60 + dt.minute`, ignoring seconds. A query at `00:15:30 UTC` was classified as inside the window despite being 30 seconds past the 15-minute boundary. | Remediated in `src/execution/reconciliation.py`: Updated calculation to total seconds from midnight (`dt.hour * 3600 + dt.minute * 60 + dt.second`) with exact $\le 900\text{s}$ boundary threshold. Verified by `test_funding_window_exact_seconds_boundaries`. | **RESOLVED** |
| **FIND-4.2.4-05** | **MEDIUM** | Orphaned OCO Sweep Router In-Memory Active Exits Out-of-Sync | When `_sweep_orphaned_oco_orders_in_cycle` cancelled orphaned companion orders on the exchange and in the database, `order_router._active_exit_orders` was not updated, leaving stale client IDs in the router's active exit tracking set. | Remediated in `src/execution/reconciliation.py`: When `self.order_router` is present, swept client IDs are discarded from `self.order_router._active_exit_orders`. Verified by unit tests in `test_reconciliation_tolerances.py`. | **RESOLVED** |

---

## 3. Detailed Audit of the 10 Critical Safety Areas

### 3.1 Area 1: JWT Authentication, ADMIN/VIEWER Authorization, and AI Lockout
- **RBAC Hierarchy:** Verified that `ROLE_HIERARCHY` (`VIEWER: 1`, `OPERATOR: 2`, `ADMIN: 3`) in `src/api/auth.py` strictly restricts mutating recovery routes (`/adopt-ghost-position`, `/operator-flatten`) to `ADMIN`. VIEWER and OPERATOR tokens attempting mutation receive `HTTP 403 Forbidden`.
- **Token Expiry:** `verify_token()` enforces `exp` claim validation against `int(time.time())`. Expired tokens return `HTTP 401 Unauthorized`.
- **Constitutional AI Lockout:** Enforced on two distinct layers:
  1. Token generation rejects AI subjects in `create_access_token` (`PermissionError`).
  2. Route handler invokes `_verify_human_operator(user, payload.operator_id)` checking both `user.sub` and `payload.operator_id` against `("ai_", "bot_", "agent_", "llm_")`, returning `HTTP 403 Forbidden` (`SECURITY VETO`).

### 3.2 Area 2: HMAC-SHA256 Canonicalization, Payload Integrity & Freshness
- **Canonical Message Strings:**
  - `ADOPT_GHOST_POSITION|{account_id}|{product_id}|{adopted_quantity}|{realized_price}|{operator_id}|{nonce}|{timestamp_iso}`
  - `OPERATOR_FLATTEN|{account_id}|{product_id}|{size}|{side}|{operator_id}|{nonce}|{timestamp_iso}`
- **Tampering Resistance:** Modifying any parameter (e.g. quantity from 1.0 to 5.0) invalidates the signature and returns `HTTP 401 Unauthorized`.
- **Timestamp Freshness:** `_verify_timestamp_freshness` checks `|now - timestamp| <= 300.0s`. Requests with expired timestamps return `HTTP 400 Bad Request`.

### 3.3 Area 3: Durable 24-Hour Idempotency & Replay Protection
- **Single-Use Nonce Consumption:** Monitored atomically via `consumed_authorization_nonces`. Duplicate nonces return `HTTP 409 Conflict`.
- **Concurrent Race Protection:** Verified under 5 concurrent parallel requests: atomic database transaction commits the first nonce while concurrent contenders hit `IntegrityError` and return `HTTP 409 Conflict`, with zero 500 errors.
- **Idempotency Key Deduplication:** Retrying with identical `idempotency_key` tokens returns `HTTP 409 Conflict`.

### 3.4 Area 4: Ghost-Position Adoption Safety
- **Exchange Evidence Alignment:** Query exchange positions to synchronize `db_pos.unrealized_pnl` and `current_mark_price` with exchange reality.
- **Atomic Database Transaction:** Foreign key validation, `Position` upsert, and `OperatorReconciliationAuditLog` creation execute in a single database transaction committed atomically.
- **Subsequent Cycle Safety:** Calling `run_reconciliation_cycle()` at the end of adoption guarantees that if the adopted quantity diverges from exchange reality, the discrepancy is immediately re-flagged.

### 3.5 Area 5: Operator Flatten Safety & Fail-Fast Guard
- **Reduce-Only Enforcement:** Market order payload strictly specifies `reduce_only: True` on the wire, preventing unintended position reversals.
- **Leadership & Drift Guards:** Checks `is_leader_valid()`. Fails fast with `HTTP 503 Service Unavailable` if clock drift $> 1000\text{ms}$ or exchange transport probe fails.
- **Protective Bypass:** Dispatched with `is_protective=True`, bypassing entry gates (Gate 11 fee check, Gate 5 position lock) while strictly enforcing Gate 1 and rate limiting.

### 3.6 Area 6: Kill-Switch Reset Discrepancy Gate
- **Immediate Pre-Reset Cycle:** If `ReconciliationDaemon` is registered, `reset_kill_switch` executes an immediate live three-way reconciliation pass. Any active discrepancies or ghost positions block reset with `HTTP 409 Conflict`.
- **Durable Snapshot Authority:** If daemon is offline, database snapshot must be $\le 60.0\text{s}$ fresh. Stale snapshots block reset with `HTTP 409 Conflict`.

### 3.7 Area 7: Funding Settlement Window Boundaries
- **Settlement Schedules:** Perpetual contracts settle at 00:00, 08:00, 16:00 UTC.
- **Boundary Precision:** Evaluates exact $\pm 900$ seconds. Confirmed:
  - `00:15:00` $\to$ True, `00:15:01` $\to$ False.
  - `23:45:00` $\to$ True, `23:44:59` $\to$ False.
  - `08:15:00` $\to$ True, `08:15:01` $\to$ False.
  - `16:15:00` $\to$ True, `16:15:01` $\to$ False.

### 3.8 Area 8: Basis-Aware UPnL Tolerance
- **Formula:** $\text{Tol}_{\text{UPnL}} = \text{PositionSize} \times \text{ContractValue} \times \max(\text{Spread}, 3.0 \times \text{BasisTWAP})$
- **Dimensional Correctness:** Contracts $\times$ (Units/Contract) $\times$ USD/Unit = USD.
- **Position Mismatch Protection:** Guarded by `if abs_pos_size > 1e-6 and abs_delta <= 1e-6:`. Position mismatches are evaluated with strict zero tolerance in Step 5a and can never be masked by UPnL tolerance.

### 3.9 Area 9: OCO Orphan Sweep
- **Exchange Cancellation:** Queries exchange for open `reduce_only` or stop orders when position is flat and dispatches cancellations.
- **Failure Resilience:** Network timeouts on individual cancellations are caught and logged; remaining orders continue processing and DB records update to `CANCELLED`.
- **Router Synchronization:** Discards swept order IDs from `order_router._active_exit_orders`.

### 3.10 Area 10: FastAPI Route Registration & Response Status Codes
- **Mounted Prefix:** `/api/v1/reconciliation` registered in `src/api/main.py`.
- **HTTP Status Code Mapping:**
  - `HTTP 200 OK`: Successful adoption, flatten dispatch, status query, snapshot query, kill switch reset.
  - `HTTP 400 Bad Request`: Expired timestamps, invalid order sides, malformed parameters.
  - `HTTP 401 Unauthorized`: Missing/invalid JWT, invalid HMAC signature.
  - `HTTP 403 Forbidden`: Non-ADMIN roles on mutations, AI principals (`SECURITY VETO`).
  - `HTTP 409 Conflict`: Nonce replay, duplicate idempotency key, active discrepancies on kill switch reset, stale snapshots.
  - `HTTP 503 Service Unavailable`: Unregistered router/daemon, clock drift $> 1000\text{ms}$, transport outage.

---

## 4. Investigation of Skipped PostgreSQL Integration Tests

During full regression test execution, two integration tests in `tests/integration/database/test_postgresql_runtime_compatibility.py` were skipped:
1. `test_postgresql_16_runtime_and_schema_compatibility` (Line 178)
2. `test_postgresql_schema_rollback_safety_preconditions` (Line 224)

### Root Cause Analysis
The test harness contains a pre-test probe:
`conn_ok, err_msg = check_postgres_connectivity(target_url)`
The probe queries PostgreSQL at `127.0.0.1:5432` targeting `gold_trading_test`.
Running `pg_isready -h 127.0.0.1 -p 5432` confirms the PostgreSQL daemon on the host is currently offline (`connection refused`).

### Audit Assessment
- **Explicit Non-Counting:** In accordance with audit instructions, these 2 skipped tests are **NOT counted as passed**.
- **Conditional Skip Behavior:** The skip decorator functions correctly as designed, safely preventing test execution against an offline server without masking failures.
- **Database Safety Invariant:** When PostgreSQL is available, the tests target only `gold_trading_test` and enforce `validate_test_database_target()`.

---

## 5. Empirical Test Execution Summary

### 5.1 Sub-Milestone Targeted Suites
Command:
```bash
./.venv/bin/pytest tests/unit/execution/test_operator_endpoints.py \
                   tests/unit/execution/test_reconciliation_adversarial.py \
                   tests/unit/execution/test_reconciliation.py \
                   tests/integration/database/test_reconciliation_integration.py \
                   tests/unit/execution/test_reconciliation_tolerances.py -v
```
Output:
```
============================== test session starts ==============================
collected 36 items

tests/unit/execution/test_operator_endpoints.py::test_auth_jwt_roles_and_token_validation PASSED [  2%]
tests/unit/execution/test_operator_endpoints.py::test_strict_ai_principal_denial PASSED [  5%]
tests/unit/execution/test_operator_endpoints.py::test_hmac_signature_verification_and_tampering PASSED [  8%]
tests/unit/execution/test_operator_endpoints.py::test_durable_nonce_consumption_and_replay_protection PASSED [ 11%]
tests/unit/execution/test_operator_endpoints.py::test_operator_flatten_fail_fast_clock_drift_and_outage PASSED [ 13%]
tests/unit/execution/test_operator_endpoints.py::test_ghost_position_adoption_and_audit_log PASSED [ 16%]
tests/unit/execution/test_operator_endpoints.py::test_kill_switch_reset_discrepancy_gate PASSED [ 19%]
tests/unit/execution/test_operator_endpoints.py::test_status_and_snapshots_endpoints PASSED [ 22%]
tests/unit/execution/test_reconciliation_adversarial.py::test_concurrent_replay_race_protection PASSED [ 25%]
tests/unit/execution/test_reconciliation_adversarial.py::test_idempotency_key_duplicate_prevention PASSED [ 27%]
tests/unit/execution/test_reconciliation_adversarial.py::test_kill_switch_reset_immediate_cycle_catches_unexplained_position PASSED [ 30%]
tests/unit/execution/test_reconciliation_adversarial.py::test_kill_switch_reset_blocked_on_stale_snapshot PASSED [ 33%]
tests/unit/execution/test_reconciliation_adversarial.py::test_funding_window_exact_seconds_boundaries PASSED [ 36%]
tests/unit/execution/test_reconciliation_adversarial.py::test_oco_sweep_resilience_to_exchange_cancel_failure PASSED [ 38%]
tests/unit/execution/test_reconciliation.py::test_reconciliation_clean_pass PASSED [ 41%]
tests/unit/execution/test_reconciliation.py::test_in_flight_correlation_buffer_within_grace PASSED [ 44%]
tests/unit/execution/test_reconciliation.py::test_in_flight_correlation_buffer_expired_missing_order PASSED [ 47%]
tests/unit/execution/test_reconciliation.py::test_unknown_exchange_order_detection PASSED [ 50%]
tests/unit/execution/test_reconciliation.py::test_terminal_state_synchronization_filled_and_cancelled PASSED [ 52%]
tests/unit/execution/test_reconciliation.py::test_partial_fill_detection_and_bracket_scaling PASSED [ 55%]
tests/unit/execution/test_reconciliation.py::test_ghost_position_detection_and_kill_switch_latch_no_auto_liquidation PASSED [ 58%]
tests/unit/execution/test_reconciliation.py::test_operator_remediation_adopt_ghost_position PASSED [ 61%]
tests/unit/execution/test_reconciliation.py::test_exchange_transport_outage_fails_closed PASSED [ 63%]
tests/unit/execution/test_reconciliation.py::test_leadership_gating_blocks_reconciliation PASSED [ 66%]
tests/unit/execution/test_reconciliation.py::test_reconciliation_daemon_start_stop_lifecycle PASSED [ 69%]
tests/integration/database/test_reconciliation_integration.py::test_multi_cycle_snapshot_persistence PASSED [ 72%]
tests/integration/database/test_reconciliation_integration.py::test_ghost_position_adoption_and_audit_log_persistence PASSED [ 75%]
tests/integration/database/test_reconciliation_integration.py::test_router_entry_blocking_when_reconciliation_detects_discrepancy PASSED [ 77%]
tests/integration/database/test_reconciliation_integration.py::test_distributed_leadership_fencing_router_and_daemon PASSED [ 80%]
tests/integration/database/test_reconciliation_integration.py::test_e2e_reconciliation_lifecycle_with_partial_fill_and_sl_resizing PASSED [ 83%]
tests/unit/execution/test_reconciliation_tolerances.py::test_funding_settlement_window_detection PASSED [ 86%]
tests/unit/execution/test_reconciliation_tolerances.py::test_funding_cash_tolerance_inside_and_outside_window PASSED [ 88%]
tests/unit/execution/test_reconciliation_tolerances.py::test_record_funding_event_updates_cash_and_logs PASSED [ 91%]
tests/unit/execution/test_reconciliation_tolerances.py::test_basis_aware_upnl_tolerance PASSED [ 94%]
tests/unit/execution/test_reconciliation_tolerances.py::test_orphaned_oco_sweep_when_position_is_flat PASSED [ 97%]
tests/unit/execution/test_reconciliation_tolerances.py::test_resting_stops_preserved_when_position_open PASSED [100%]

============================== 36 passed in 4.39s ==============================
```

### 5.2 Full Repository Regression Suite
Command:
```bash
./.venv/bin/pytest -v -rs
```
Output Summary:
```
================== 353 passed, 2 skipped, 1 warning in 25.53s ==================
Exit Code: 0
```
- **Passed:** 353
- **Failed:** 0
- **Errors:** 0
- **Skipped:** 2 (Conditional skips for PostgreSQL runtime tests when host daemon offline)
- **Warnings:** 1 (Starlette deprecation warning in paper WebSocket test)
- **Exit Code:** 0

---

## 6. Guardrails & Constitutional Invariants Verification

| Invariant | Constitutional Mandate | Audit Verification Evidence | Status |
|---|---|---|---|
| **Live Trading Gate** | `LIVE_TRADING_ENABLED = False` hardcoded | Verified line 56 in `src/core/config.py`: `LIVE_TRADING_ENABLED: bool = False` | **ENFORCED** |
| **Decision A Status** | Decision A remains strictly **OPEN** | Zero live accounts provisioned; zero socket calls to Delta Testnet | **PRESERVED** |
| **Network Isolation** | Zero live exchange / Testnet calls | All tests executed against offline `MockDeltaExchangeServer` or mock transports | **ENFORCED** |
| **Frozen Files Integrity** | Zero modifications to Phase 1–3 files | `git diff src/core/config.py src/risk/kill_switch.py src/risk/paper_risk_engine.py` is empty | **ENFORCED** |
| **Git Working Tree** | No commits, tags, pushes, or resets performed | Working tree preserved; HEAD at `054cd00d4219e804c2b1ec5859c65a81e1892108` | **ENFORCED** |
| **Scope Boundary** | Do not begin Sub-Milestone 4.2.5 | No code from Sub-Milestone 4.2.5 has been implemented | **ENFORCED** |

---

## 7. Residual Risks

1. **Host NTP Synchronization in Production:**
   - On a live Linux VPS, NTP synchronization must be continuously maintained. If local host time drifts $> 1000\text{ms}$ from Delta Exchange, automated operator flattening will fail fast (`HTTP 503`), requiring manual out-of-band intervention via Delta Web Console.
2. **PostgreSQL 16 Daemon Requirement for Live DB Tests:**
   - The two skipped PostgreSQL runtime integration tests require an active PostgreSQL 16 container targeting `gold_trading_test`. All SQLite and in-memory unit/integration tests pass cleanly with zero errors.

---

## 8. Final Audit Verdict

### **VERDICT: PASS**

Sub-Milestone 4.2.4 satisfies all constitutional safety invariants, architectural design specifications, and implementation plan acceptance criteria. All critical authentication, replay prevention, risk gate, position safety, and database persistence properties have been independently verified.

