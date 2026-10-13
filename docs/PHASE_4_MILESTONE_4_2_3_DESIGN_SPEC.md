# AGentTrXPAUS — Sub-Milestone 4.2.3 Design Specification
## Distributed Leadership Leases & Three-Way Continuous Reconciliation Daemon

**Document:** `docs/PHASE_4_MILESTONE_4_2_3_DESIGN_SPEC.md`  
**Sub-Milestone:** 4.2.3 (Distributed Leadership Leases & Reconciliation Daemon)  
**Status:** DESIGN & SPECIFICATION FREEZE  
**Governance:** `PROJECT_CONSTITUTION.md`, `ARCHITECTURE.md`, `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md`  
**Date:** 2026-10-12  

---

## 1. Executive Summary & Architectural Scope

Sub-Milestone 4.2.3 establishes the institutional reliability and recovery layer for live execution on Delta Exchange. It unifies two critical architectural components:
1. **PostgreSQL Distributed Leadership Leases (`LeaderLeaseManager`):** Guarantees singleton execution across distributed nodes, containers, or worker processes. Utilizes monotonic fencing tokens (`lease_epoch`) evaluated strictly against the database server clock (`CURRENT_TIMESTAMP`), completely eliminating split-brain execution.
2. **Three-Way Continuous Reconciliation Daemon (`ReconciliationDaemon`):** Executes an automated, deterministic audit loop reconciling:
   $$\text{In-Memory Engine State} \iff \text{Authoritative PostgreSQL Database} \iff \text{Delta Exchange REST API}$$
   Equipped with a 30-second in-flight correlation buffer, discrete zero-tolerance position quantity checks, dynamic funding-aware cash tolerances, basis-aware UPnL tolerances, and strict fail-closed ghost exposure protocols.

---

## 2. Authoritative State Hierarchy & System of Record

```text
+---------------------------------------------------------------+
|                 1. Delta Exchange Matching Engine             |
|   (Ultimate authority for resting orders, fills, and wallet)   |
+-------------------------------+-------------------------------+
                                |
                                v
+---------------------------------------------------------------+
|                 2. Durable PostgreSQL Database                 |
|   (System of record for local intent, strategy state, leases) |
+-------------------------------+-------------------------------+
                                |
                                v
+---------------------------------------------------------------+
|                 3. In-Memory Trading Engine State             |
|   (Ultra-low latency runtime working cache; transient)        |
+-------------------------------+-------------------------------+
                                |
                                v
+---------------------------------------------------------------+
|                 4. Ephemeral Redis Pub/Sub Cache               |
|   (Event distribution only; zero authoritative state)         |
+-------------------------------+-------------------------------+
```

### 2.1 Database Entities (System of Record)
- **`live_orders`:** Durable order intents, states (`CREATED`, `SIGNED`, `SUBMITTED`, `ACCEPTED`, `PARTIALLY_FILLED`, `FILLED`, `REJECTED`, `CANCELLED`, `EXPIRED`, `FAILED`, `RECONCILING`), quantities, prices, and exchange identifiers.
- **`live_order_events`:** Immutable audit trail recording every state transition (`from_state`, `to_state`, `event_type`, `payload`, `timestamp`).
- **`positions`:** Portfolio position records tagged with `mode='LIVE'`, `live_account_id`, `quantity`, `entry_price`, `current_price`, and unrealized PnL.
- **`leader_leases`:** Singleton lease records (`resource_name`, `owner_id`, `lease_epoch`, `acquired_at`, `expires_at`).
- **`reconciliation_snapshots`:** Immutable log of every reconciliation cycle (`snapshot_id`, `account_id`, `timestamp`, `discrepancy_count`, `cash_local`, `cash_exchange`, `upnl_local`, `upnl_exchange`, `status`, `details`).
- **`operator_reconciliation_audit_logs`:** Audit log for manual interventions (`adopt_ghost_position`, `operator_flatten`).

---

## 3. Distributed Leadership Leases & Fencing Tokens

### 3.1 Lease Invariants
1. **Server Clock Absolute Authority:** All lease acquisitions, renewals, and validity checks use PostgreSQL server time (`CURRENT_TIMESTAMP`). Local system/container clocks are completely untrusted.
2. **Monotonic Fencing Tokens:** Every new lease acquisition strictly increments `lease_epoch = lease_epoch + 1`. All subsequent write queries carry this token:
   $$\text{UPDATE / INSERT} \quad \text{WHERE} \quad \text{lease\_epoch} = :current\_epoch \quad \text{AND} \quad \text{expires\_at} > \text{CURRENT\_TIMESTAMP}$$
3. **Pre-Flight Dispatch Lease Check:** The order router and reconciliation daemon evaluate `is_leader_valid()`. If remaining lease TTL $< 2.0\text{s}$, execution halts until heartbeat renewal succeeds.
4. **Heartbeat & Resignation:**
   - Active leader issues heartbeats every 5.0 seconds.
   - Lease TTL is 15.0 seconds.
   - On graceful shutdown, leader resigns by setting `expires_at = CURRENT_TIMESTAMP`.
   - On crash, standby nodes wait out the 15.0-second TTL before attempting acquisition with an incremented epoch.

---

## 4. Delta Exchange API Contracts & Snapshot Specifications

### 4.1 Required Delta Exchange Endpoints
1. **`GET /v2/orders` (Open Orders):**
   - Returns list of resting active orders on Delta Exchange matching engine.
   - Fields: `id` (int), `client_order_id` (str), `product_id` (int), `size` (int/float), `unfilled_size` (int/float), `side` (str), `order_type` (str), `state` (str), `created_at` (str).
2. **`GET /v2/positions` (Open Positions):**
   - Returns open derivative positions.
   - Fields: `product_id` (int), `size` (float), `entry_price` (float), `mark_price` (float), `realized_pnl` (float), `unrealized_pnl` (float).
3. **`GET /v2/wallet/balances` (Balances):**
   - Returns wallet asset balances.
   - Fields: `asset_symbol` (str), `balance` (float/str), `available_balance` (float/str).
4. **`POST /v2/orders/cancel` (Order Cancellation):**
   - Cancels resting order.
   - Returns error HTTP 400 `OrderAlreadyFilled` if order already completed.

### 4.2 Stale-Snapshot & Network Failure Protections
- **Snapshot Timeout:** Exchange poll timeout is bounded at 3.0 seconds.
- **Retry Backoff:** Exponential backoff with jitter on network disconnects (1s, 2s, 4s, capped at 10s).
- **Transport Outage Guard:** If Delta REST is unreachable for $\ge 3$ consecutive cycles, reconciliation flags `EXCHANGE_TRANSPORT_OUTAGE`, maintains existing safety locks, and halts automated mutations.

---

## 5. Three-Way Reconciliation Rules & Discrepancy Matrix

### 5.1 In-Flight Order Correlation Buffer ($T_{\text{grace}} = 30\text{s}$)
To prevent false-positive alarms caused by network propagation delays:
- Discrepancy between local position and exchange position $\Delta Q = Q_{\text{exchange}} - Q_{\text{db}}$ is evaluated against active orders created within the last 30 seconds.
- If $\Delta Q \le \sum Q_{\text{in\_flight}}$, the discrepancy is classified as `IN_FLIGHT_FILL_PENDING`.
- Alarms are deferred for up to 2 cycles (20 seconds). If uncommitted after 20 seconds, the daemon issues a direct REST query on the `client_order_id` to force-resolve state.

### 5.2 Discrepancy Classification & Action Matrix

| Discrepancy Type | Condition | Resolution Action | Order / Engine State |
| :--- | :--- | :--- | :--- |
| **Missing Order (In-Flight)** | Local DB `SUBMITTED`/`ACCEPTED`; Exchange REST returns 404/empty; Age $\le 30\text{s}$. | Defer alarm for in-flight grace period. | Unchanged (`SUBMITTED`/`ACCEPTED`). |
| **Missing Order (Expired)** | Local DB `SUBMITTED`/`ACCEPTED`; Exchange REST returns 404/empty; Age $> 30\text{s}$. | Transition local DB order to `FAILED -> RECONCILING`. Increment `discrepancy_count`. | `RECONCILING`. Entry orders blocked. |
| **Unknown Order** | Order with prefix `AGY_` exists on exchange; missing from local DB. | Log `UNKNOWN_EXCHANGE_ORDER` discrepancy. Increment `discrepancy_count`. Do NOT delete exchange order blindly. | Discrepancy flagged. Entry orders blocked. |
| **Partial Fill Desync** | Exchange reports `unfilled_size < size`; local DB still shows `size` unfilled. | Update `filled_quantity` in DB, transition order to `PARTIALLY_FILLED`. Trigger bracket stop scaling ($Q_{\text{SL}} = Q_{\text{filled}}$). | `PARTIALLY_FILLED`. |
| **Terminal State Desync** | Local DB shows `ACCEPTED`; Exchange reports `cancelled` or `filled`. | Align local DB to true exchange state (`CANCELLED` or `FILLED`). Persist transition in `live_order_events`. | `CANCELLED` / `FILLED`. |
| **Bracket Size Mismatch** | Active Stop Loss size $Q_{\text{SL}} \ne Q_{\text{position}}$. | Issue cancel/replace on exchange stop order to enforce $Q_{\text{SL}} = Q_{\text{position}}$. | Old stop `CANCELLED`, new scaled stop `ACCEPTED`. |
| **Ghost Position** | $Q_{\text{exchange}} \ne Q_{\text{db}}$ with ZERO matching in-flight orders. | **CRITICAL:** ZERO automated liquidation. Latch Kill Switch to `MANUAL_RESET_REQUIRED`. Transition engine to `NEW_TRADING_BLOCKED`. Emit critical operator alert. | `NEW_TRADING_BLOCKED`. Kill Switch latched. |

---

## 6. Safety Requirements & Constitutional Guardrails

1. **Zero Blind Resubmissions:** Orders with unknown outcomes (`httpx.TimeoutException`, network drop) are escalated to `RECONCILING` and resolved via read queries. Never blindly resubmitted.
2. **Zero Automated Ghost Liquidations:** Unexplained exchange exposure halts trading and alerts operators. Blind market orders are strictly forbidden to avoid crossing wide spreads, doubling exposure on stale data, or paying taker fees.
3. **Discrepancy Gating:** `LiveOrderRouter.discrepancy_count` is updated dynamically by `ReconciliationDaemon`. Any unresolved discrepancy ($> 0$) unconditionally blocks new entries (`DiscrepancyBlockedError`).
4. **Protective Exit Preservation:** Reconciliation actions never cancel or discard resting protective stop-loss orders unless performing an atomic cancel/replace with a verified replacement.
5. **Durable Database Persistence:** All reconciliation decisions, state changes, and snapshots are durably committed in PostgreSQL.

---

## 7. Lifecycle, Startup & Crash Recovery Sequence

1. **Startup / Restart Sequence:**
   - Initialize database session factory.
   - Acquire leadership lease on `live_reconciliation_leader`.
   - Reconstruct router state via `LiveOrderRouter.recover_state()`.
   - Execute initial synchronous three-way reconciliation pass:
     - Query Delta Exchange open orders, positions, and balances.
     - Match active database orders against exchange state.
     - Verify position alignment.
     - Populate initial `ReconciliationSnapshot`.
   - If clean ($\text{DiscrepancyCount} == 0$), mark engine operational.
   - Start background periodic reconciliation loop (10-second cadence).
2. **Periodic Loop:**
   - Check leadership validity (`is_leader_valid()`). If lost, skip iteration.
   - Issue heartbeat to renew lease.
   - Execute three-way reconciliation audit.
   - Persist `ReconciliationSnapshot`.
   - Update router `discrepancy_count`.
3. **Shutdown Sequence:**
   - Cancel periodic task.
   - Resign leader lease cleanly.

---

## 8. Test Strategy & Acceptance Criteria

### Test Coverage Requirements
1. **Leadership Leases:**
   - Single worker acquires and renews lease.
   - Two concurrent workers contend for lease; exactly one wins.
   - Lease expires on leader crash; standby claims lease with incremented `lease_epoch`.
   - Fenced queries block stale writes when `lease_epoch` does not match.
   - Pre-flight check rejects dispatch when TTL $< 2.0\text{s}$.
2. **Reconciliation Daemon:**
   - Clean alignment test: zero discrepancies produces `CLEAN` snapshot.
   - In-flight correlation: fill within 30s grace window is not flagged as Ghost.
   - Ghost position detection: unexplained exchange position blocks entries and latches Kill Switch without automated liquidation.
   - Missing order handling: expired in-flight order escalates to `RECONCILING`.
   - Terminal state desync: exchange fill/cancel is propagated to local database.
   - Partial fill bracket scaling: reconciles and resizes resting stops.
   - Exchange transport failure: fails safe without corrupting local database.
   - Duplicate reconciliation runs: idempotent with zero duplicate events.
   - Restart recovery: rehydrates discrepancies and recovers state cleanly.

