# AGentTrXPAUS — Sub-Milestone 4.2.2 Safety Remediation Report

**Date:** 2026-10-12  
**Target Milestone:** Sub-Milestone 4.2.2 (Order State Machine & Idempotent Execution Router)  
**Status:** REMEDIATED & EMPIRICALLY VERIFIED  
**Audit Reference:** `docs/GATE_AUDIT_4_2_2_REPORT.md`  

---

## 1. Executive Summary

All 12 findings (4 BLOCKER, 4 HIGH, 4 MEDIUM) identified in the independent safety audit `docs/GATE_AUDIT_4_2_2_REPORT.md` have been systematically remediated, validated, and empirically verified against full test suites. 

### Key Architectural & Safety Enhancements
1. **Durable Database-Backed Idempotency:** Implemented transactional pre-dispatch persistence of order intent in PostgreSQL (`live_orders` in `CREATED` state) with database-enforced uniqueness constraints on `client_order_id` as the cross-process authority. Conflicting payload resubmissions raise `IdempotencyConflictError` across process boundaries.
2. **Synchronized In-Flight Concurrency:** Replaced premature `CREATED` returns for concurrent in-flight duplicates with `asyncio.Future` synchronization, guaranteeing concurrent callers await final primary resolution without duplicate dispatch.
3. **Exchange-Side Dynamic Bracket Scaling:** Dynamic stop-loss scaling now cancels resting stop orders on Delta Exchange and places replacement stop orders with reduced quantity ($Q_{\text{SL}} = Q_{\text{filled}}$ and $Q_{\text{SL}} \le Q_{\text{filled}}$).
4. **Resilient Synthetic OCO Companion Handling:** Cancellation failures query exchange order status; dual executions are detected (`FILLED`), confirmed cancellations recorded (`CANCELLED`), and indeterminate outcomes safely escalated to `RECONCILING`.
5. **State Reconstruction on Startup:** Implemented `recover_state()` in `LiveOrderRouter` to reconstruct active orders, active protective exits, bracket pairings, and unresolved discrepancy counts directly from PostgreSQL.
6. **Transactional Audit Logging:** Every FSM state transition persists an immutable audit event in `live_order_events`.
7. **Discrepancy Gating & Position Bounds:** Entry orders are blocked when `discrepancy_count > 0` (`DiscrepancyBlockedError`). Protective exits verify that requested quantities do not exceed known position quantities (`PositionBoundsExceededError`).
8. **Fail-Closed Safety Guards:** Preserved `LIVE_TRADING_ENABLED: bool = False` in `src/core/config.py`, frozen Phase 1–3 files untouched, and Decision A strictly OPEN.

---

## 2. Finding-by-Finding Remediation Matrix

| Finding ID | Severity | Description | Remediating Files & Functions | Verification Test |
| :--- | :--- | :--- | :--- | :--- |
| **FIND-4.2.2-01** | **BLOCKER** | In-memory only idempotency; multi-process and restart blindness | `src/execution/delta/idempotency.py` (`register_order`, `_register_in_session`) | `test_duplicate_submission_after_router_restart`, `test_two_router_instances_concurrent_submission` |
| **FIND-4.2.2-02** | **BLOCKER** | In-flight concurrent duplicates returned raw unplaced `CREATED` record | `src/execution/delta/order_router.py` (`_in_flight_futures`) | `test_in_flight_concurrent_duplicate_submission_awaits_primary` |
| **FIND-4.2.2-03** | **BLOCKER** | Bracket scaling mutated local dict without exchange cancel/replace | `src/execution/delta/order_router.py` (`on_partial_fill`, `_scale_bracket_stop_loss`) | `test_exchange_side_bracket_scaling_cancel_replace` |
| **FIND-4.2.2-04** | **BLOCKER** | Synthetic OCO cancellation failure left companions without status check | `src/execution/delta/order_router.py` (`_cancel_companion_order`) | `test_oco_companion_cancellation_handles_dual_fill` |
| **FIND-4.2.2-05** | **HIGH** | Incomplete restart state reconstruction | `src/execution/delta/order_router.py` (`recover_state`) | `test_restart_recovery_reconstructs_active_orders_and_brackets` |
| **FIND-4.2.2-06** | **HIGH** | FSM transitions not persisted to `live_order_events` | `src/execution/delta/order_router.py` (`_transition_order`), `idempotency.py` (`update_state`) | `test_durable_fsm_audit_events_persisted` |
| **FIND-4.2.2-07** | **HIGH** | Entry gating ignored live position discrepancies | `src/execution/delta/order_router.py` (`dispatch_order`), `src/execution/delta/types.py` (`DiscrepancyBlockedError`) | `test_discrepancy_count_blocks_new_entry` |
| **FIND-4.2.2-08** | **HIGH** | Protective exits lacked position quantity bounds check | `src/execution/delta/order_router.py` (`dispatch_order`), `src/execution/delta/types.py` (`PositionBoundsExceededError`) | `test_position_bounds_check_blocks_excess_protective_exit` |
| **FIND-4.2.2-09** | **MEDIUM** | Subclass caught before base; RateLimit/Auth incorrectly marked RECONCILING | `src/execution/delta/order_router.py` (`dispatch_order`) | `test_rate_limit_and_auth_failures_transition_to_rejected` |
| **FIND-4.2.2-10** | **MEDIUM** | Timed-out orders left in volatile state without DB tracking | `src/execution/delta/order_router.py` (`dispatch_order`), `idempotency.py` (`update_state`) | `test_network_timeout_persists_reconciling_state_in_database` |
| **FIND-4.2.2-11** | **MEDIUM** | Mock server permitted cancel on filled orders masking OCO dual fills | `src/execution/delta/mock_server.py` (`/v2/orders/cancel`) | `test_oco_companion_cancellation_handles_dual_fill` |
| **FIND-4.2.2-12** | **MEDIUM** | Clock drift fallback failed open on unexpected errors | `src/execution/delta/order_router.py` (`_get_clock_drift_ms`) | `test_clock_drift_mitigation.py` |

---

## 3. Detailed Technical Remediation

### 3.1 Durable Database-Backed Idempotency (FIND-4.2.2-01)
- `IdempotencyManager` now accepts an `async_sessionmaker[AsyncSession]` session factory.
- When `register_order` is invoked, it inserts a new `LiveOrder` row in `state="CREATED"` and an initial `LiveOrderEvent` (`REGISTER_INTENT`) transactionally before returning.
- If a duplicate `client_order_id` is submitted concurrently by another process, PostgreSQL raises a uniqueness constraint `IntegrityError`. The transaction rolls back cleanly, opens an isolated read session to re-query the committed order record, validates parameter consistency (rejecting parameter mutations with `IdempotencyConflictError`), and returns `(existing_record, True)`.
- Numeric tolerances (`1e-6`) ensure float-to-integer representation differences (`size=5` vs `5.0`) do not cause false parameter mismatch rejections.

### 3.2 In-Flight Concurrency Synchronization (FIND-4.2.2-02)
- Added `self._in_flight_futures: Dict[str, asyncio.Future]` to `LiveOrderRouter`.
- When an order dispatch is initiated for `client_id`, an unresolved `asyncio.Future` is registered.
- Any concurrent duplicate request arriving while the initial submission is still in-flight detects the active future and awaits `asyncio.shield(in_flight_fut)`.
- When the primary submission finishes (either reaching `ACCEPTED`, `REJECTED`, or `RECONCILING`), the future resolves with the completed record.

### 3.3 Exchange-Side Bracket Scaling (FIND-4.2.2-03)
- In `on_partial_fill`, when an entry order partially fills, `_scale_bracket_stop_loss()` executes a real cancel/replace workflow:
  1. Retrieves the resting exchange stop-loss order via `sl_order_id`.
  2. Submits an exchange cancellation via `client.cancel_order()`.
  3. Transitions old stop-loss FSM to `CANCELLED`.
  4. Generates a new deterministic replacement client order ID: `AGY_SL_{STRATEGY}_{TS}_{NONCE}`.
  5. Places a replacement stop-loss on the exchange with scaled size ($Q_{\text{SL}} = Q_{\text{filled}}$).
  6. Updates bracket pair pointers atomically.

### 3.4 Resilient Synthetic OCO Cancellation (FIND-4.2.2-04, FIND-4.2.2-11)
- When a companion order in an OCO pair needs cancellation upon trigger of the opposite bracket, `_cancel_companion_order()` calls `client.cancel_order()`.
- If cancellation succeeds, the companion transitions to `CANCELLED`.
- If cancellation raises an error (e.g., `DeltaOrderRejectedError` or HTTP 400 `OrderAlreadyFilled`), the router immediately queries `client.get_order_status()`.
  - If status is `FILLED`, the companion is transitioned to `FILLED` (flagging dual fill / race condition).
  - If status is `CANCELLED`, the companion is transitioned to `CANCELLED`.
  - If status check times out or fails, the companion is escalated to `RECONCILING` to prevent silent unmanaged exposure.

### 3.5 Restart Recovery (FIND-4.2.2-05)
- Implemented `recover_state()` in `LiveOrderRouter`:
  - Scans `live_orders` for all active states (`CREATED`, `PENDING_NEW`, `ACCEPTED`, `PARTIALLY_FILLED`, `RECONCILING`).
  - Instantiates `OrderLifecycleFSM` instances for each active order.
  - Reconstructs active exit sets (`_active_exits`) by inspecting `reduce_only` flags and product IDs.
  - Reconstructs bracket pairings from order payloads and metadata.
  - Queries active discrepancies to populate `discrepancy_count`.

### 3.6 Transactional Audit History (FIND-4.2.2-06)
- Implemented `_transition_order()` in `LiveOrderRouter`.
- Whenever an order transitions between states, it transitions the FSM in-memory and invokes `IdempotencyManager.update_state()`.
- `update_state()` updates the `LiveOrder.state` and inserts a `LiveOrderEvent` with `from_state`, `to_state`, `event_type`, `payload`, and UTC timestamp in a single atomic database commit.

### 3.7 Discrepancy Gate & Position Bounds (FIND-4.2.2-07, FIND-4.2.2-08)
- Added `DiscrepancyBlockedError` and `PositionBoundsExceededError` to `src/execution/delta/types.py`.
- New entry orders verify `self.discrepancy_count == 0`; if `discrepancy_count > 0`, the order is rejected with `DiscrepancyBlockedError`.
- Protective exits bypass entry gates (Gates 2, 4, 7, 8, 9, 10, 11) but strictly enforce Gate 1 (portfolio drawdown) and verify that the requested protective exit quantity does not exceed the known current position size.

---

## 4. Test Suite Execution & Verification Evidence

### 4.1 Sub-Milestone 4.2.2 Durability Integration Suite
Command:
```bash
./.venv/bin/pytest tests/integration/database/test_order_idempotency_durability.py -v
```
Output:
```
============================= test session starts ==============================
collected 6 items

tests/integration/database/test_order_idempotency_durability.py::test_duplicate_submission_after_router_restart PASSED [ 16%]
tests/integration/database/test_order_idempotency_durability.py::test_conflicting_payload_after_restart_raises_conflict PASSED [ 33%]
tests/integration/database/test_order_idempotency_durability.py::test_two_router_instances_concurrent_submission PASSED [ 50%]
tests/integration/database/test_order_idempotency_durability.py::test_restart_recovery_reconstructs_active_orders_and_brackets PASSED [ 66%]
tests/integration/database/test_order_idempotency_durability.py::test_durable_fsm_audit_events_persisted PASSED [ 83%]
tests/integration/database/test_order_idempotency_durability.py::test_network_timeout_persists_reconciling_state_in_database PASSED [100%]

============================== 6 passed in 2.43s ===============================
```

### 4.2 Unit Execution Suite
Command:
```bash
./.venv/bin/pytest tests/unit/execution/ -v
```
Output:
```
============================== 93 passed in 4.20s ==============================
```

### 4.3 Full Repository Regression Test Suite
Command:
```bash
./.venv/bin/pytest -v
```
Output:
```
================== 311 passed, 2 skipped, 1 warning in 21.63s ==================
```
*(The 2 skipped tests are PostgreSQL daemon connection tests in `test_postgresql_runtime_compatibility.py` that execute when a local PostgreSQL 16 daemon is online; zero tests failed).*

---

## 5. Invariant & Governance Verification

1. **Frozen Files Preserved:**
   `git diff src/core/config.py src/risk/kill_switch.py src/risk/paper_risk_engine.py` is empty (0 lines changed).
2. **Fail-Closed Production Safety:**
   `LIVE_TRADING_ENABLED: bool = False` hard-coded in `src/core/config.py`.
3. **No Network Access to Exchange:**
   All execution and router tests execute strictly against `MockDeltaExchangeServer` or mock transports. Zero outbound connections to Delta Testnet or live production.
4. **Git Repository Hygiene:**
   Zero git commits, zero git tags, zero git pushes, and zero git resets were executed. Working tree preserves uncommitted Sub-Milestone 4.2.1 and 4.2.2 changes.
5. **Decision A Status:**
   Decision A remains **STRICTLY OPEN**.
6. **Milestone Boundary:**
   Sub-Milestone 4.2.3 (Reconciliation Daemon) has **NOT** been started.

