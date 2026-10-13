# INDEPENDENT FORENSIC DESIGN AUDIT REPORT — PHASE 4, MILESTONE 4.2
## Document Under Audit: `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md`
**System:** AGentTrXPAUS — Gold AI Quantitative Trading System  
**Auditor:** Independent Senior Quant Systems Auditor, Financial Risk Officer & Exchange Architecture Reviewer  
**Audit Conducted By:** Independent Subagent (`Independent Quant Systems Auditor`)  
**Date:** October 9, 2026  
**Governing Baseline:**
- Git Branch: `release1.1v`
- Baseline Commit: `054cd00d4219e804c2b1ec5859c65a81e1892108` (`v1.3.0-phase3-verified`)
- Safety Invariant: `LIVE_TRADING_ENABLED: bool = False` (Verified in `src/core/config.py`)
**Audit Scope:** Milestone 4.2 Design Specification (FSM, Three-Way Reconciliation, Fee Architecture, Concurrency Leases, Decision A Protocol, Risk Gates)

---

## 1. OFFICIAL AUDIT VERDICT

```text
================================================================================
                     MILESTONE 4.2 DESIGN AUDIT VERDICT
================================================================================
            🔴 DESIGN REWORK REQUIRED — REMEDIATION NEEDED
================================================================================
  Summary of Findings:
    - P0 (Critical Blocker):                3 Findings
    - P1 (Major Financial / Safety Risk):    8 Findings
    - P2 (Material Architecture Gap):       6 Findings
    - P3 (Minor Clarification / Doc):       2 Findings
  Total Findings: 19
================================================================================
  Core Verdict Rationale:
  The Milestone 4.2 Design Specification establishes an institutional structural
  framework, but contains critical design flaws that would introduce catastrophic
  financial risk, operational deadlocks, and severe regression if implemented as written:
  1. The 10 Risk Gates dropped the Peak-to-Trough Drawdown Cap and Dollar-Risk Sizing,
     causing a major safety regression against Phase 3 and Phase 4 Design Report baselines.
  2. The 10-second continuous reconciliation cycle contains a fatal race condition that
     flags in-flight/asynchronously filled orders as "Ghost Positions", latching the Kill Switch.
  3. The Ghost Position policy contains an operator recovery deadlock: an operator HMAC reset
     will infinitely re-trip the Kill Switch within 10 seconds because no state adoption or
     manual liquidation remediation workflow is specified.
  4. The 11-State FSM cannot transition from RECONCILING to CANCELLED, PARTIALLY_FILLED, or EXPIRED,
     guaranteeing a crash or permanent execution freeze upon network recovery.
  5. The PostgreSQL fencing token check ignores lease expiration (expires_at), permitting
     split-brain/zombie workers to mutate the database after lease TTL expiry.
  6. The fee engine mandates strictly non-negative fees, which crashes and rejects valid
     negative maker rebates for VIP tiers and market makers.
  7. The 8-hour perpetual funding payment is not exempted from the wallet tolerance band,
     causing scheduled funding payments to breach cash tolerance and freeze trading.
================================================================================
```

---

## 2. SUMMARY OF FINDINGS REGISTER

| Finding ID | Classification | Dimension | Title / Summary | Blocks Acceptance? |
| :--- | :--- | :--- | :--- | :--- |
| **AUD-P0-01** | **P0 (Critical Blocker)** | Reconciliation | False-Positive Ghost Position Panic on In-Flight & Asynchronous Fills | **YES** |
| **AUD-P0-02** | **P0 (Critical Blocker)** | Operations / Recovery | Operator Recovery Deadlock & Infinite Ghost Loop (Missing State Resolution) | **YES** |
| **AUD-P0-03** | **P0 (Critical Blocker)** | Risk Gates | Omission of Portfolio Drawdown Cap from 10 Live Risk Gates (Severe Regression) | **YES** |
| **AUD-P1-01** | **P1 (Major Risk)** | FSM Lifecycle | `RECONCILING` State Cannot Transition to `CANCELLED`, `PARTIALLY_FILLED`, or `EXPIRED` | **YES** |
| **AUD-P1-02** | **P1 (Major Risk)** | Concurrency | Fencing Token Ignores Lease Expiration (`expires_at` Bypassed on Mutating Writes) | **YES** |
| **AUD-P1-03** | **P1 (Major Risk)** | Fee Model | Self-Contradiction on Negative Maker Fees (Rebates Treated as Malformed Data) | **YES** |
| **AUD-P1-04** | **P1 (Major Risk)** | Reconciliation | 8-Hour Perpetual Funding Payment Breaches Cash Balance Tolerance Band | **YES** |
| **AUD-P1-05** | **P1 (Major Risk)** | Decision A | Testnet Liquidity Vacuum on `PAXGUSDT` (Tests 2, 3, 5, 6 Impracticable Without Bot) | **YES** |
| **AUD-P1-06** | **P1 (Major Risk)** | Decision A | Synthetic Bracket OCO Companion Leg Leak (Old TP Fills Against New Position) | **YES** |
| **AUD-P1-07** | **P1 (Major Risk)** | Fee Model | Stale Fee Cache Overreach Blocks Protective Stops and Emergency Exits | **YES** |
| **AUD-P1-08** | **P1 (Major Risk)** | Risk Gates | Omission of Dollar-Risk Sizing Clamping and Order Rejection Burst Gate | **YES** |
| **AUD-P2-01** | **P2 (Material Gap)** | Architecture / Baselines| Kill Switch FSM Redefinition Violates Frozen Phase 3 Codebase & Drops Emergency Flatten | No (Rework needed) |
| **AUD-P2-02** | **P2 (Material Gap)** | Concurrency | Distributed Lease Clock Skew (Database Server Clock vs Worker Host Clock) | No (Rework needed) |
| **AUD-P2-03** | **P2 (Material Gap)** | Concurrency | Fencing Token SQL Syntax Incompatible with `INSERT` Operations | No (Rework needed) |
| **AUD-P2-04** | **P2 (Material Gap)** | Durability / DB | Database Schema Friction in `Position.account_id` Foreign Key Constraint | No (Rework needed) |
| **AUD-P2-05** | **P2 (Material Gap)** | Reconciliation | Dynamic Unrealized PnL Tolerance Flaw (Mark Price Basis Divergence) | No (Rework needed) |
| **AUD-P2-06** | **P2 (Material Gap)** | Fee Model | Hazardous Fee Rate Auto-Update from Single Fill Discrepancies | No (Rework needed) |
| **AUD-P3-01** | **P3 (Documentation)**| Durability / DB | Schema Naming Inconsistency (`account_fees` vs `account_fee_schedules`) | No |
| **AUD-P3-02** | **P3 (Documentation)**| FSM Lifecycle | Missing Pre-Route Rejection Transition from `CREATED` State | No |

---

## 3. FORENSIC AUDIT BY EVALUATION DIMENSION

### Dimension 1: Contradictory Requirements & Architectural Friction
1. **Drawdown Circuit Breaker Disappearance:**
   - *Baseline Evidence:* `docs/PHASE_4_DESIGN_REPORT.md` Section 9 establishes Gate 1 as `Portfolio Drawdown Cap (10.0%) -> Unconditional Kill Switch Trigger (FLATTEN_ALL)`. In Phase 3 (`src/risk/paper_risk_engine.py` line 104), Gate 1 is `max_drawdown_pct = 10.0%`.
   - *Spec Conflict:* `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` Section F.1 replaced Gate 1 with `Production Safety Hard-Lock: LIVE_TRADING_ENABLED == False`. Portfolio Peak-to-Trough Drawdown is completely dropped from the gates table! If an account loses 2.5% per day for 4 consecutive days (10% total drawdown), Gate 4 (Daily Loss) never trips, and NO OTHER GATE TRIPS. The account can be completely wiped out.
2. **Kill Switch & Frozen Phase 3 Codebase Modification:**
   - *Baseline Evidence:* `PROJECT_CONSTITUTION.md` Section 7 and M4.2 Section A.2 explicitly mandate: "No modification to frozen Phase 1, Phase 2, or Phase 3 production code." `src/risk/kill_switch.py` is frozen in Phase 3 commit `054cd00d4219e804c2b1ec5859c65a81e1892108` and tested in `tests/unit/risk/test_kill_switch_and_risk_engine.py`.
   - *Spec Conflict:* M4.2 Section A.2 line 39 targets `src/risk/kill_switch.py` for modification, and Section F.2 replaces the 5-state machine (`NORMAL -> KILL_SWITCH_TRIGGERED -> NEW_TRADING_BLOCKED -> POSITIONS_FLATTENED -> MANUAL_RESET_REQUIRED`) with a 4-state machine that deletes `POSITIONS_FLATTENED`. Modifying this directly mutates frozen Phase 3 code and breaks existing unit tests. Phase 4 must introduce `LiveKillSwitchFSM` in `src/risk/live_kill_switch.py` without mutating Phase 3 files.
3. **Emergency Flattening Contradiction:**
   - In `docs/PHASE_4_DESIGN_REPORT.md` Section 10, emergency flattening (`FLATTEN_ALL` with `reduce_only: true`) is mandated for operator emergency or hard drawdown. In M4.2 Section C.4 & F.2, automated market liquidation is completely banned to avoid ghost position hazards. While banning liquidation of *ghost* positions is sound, banning emergency liquidation of *legitimate, recognized* positions leaves open positions unhedged and exposed during flash crashes or operator kill events.

### Dimension 2: 11-State Order Lifecycle FSM Flaws
1. **Missing Recovery Transitions from `RECONCILING`:**
   - In Section D.1 (lines 284-288), `RECONCILING` only transitions to `ACCEPTED`, `FILLED`, and `REJECTED`.
   - If an order in `FAILED` (due to transport timeout) was an IOC order whose remainder was cancelled, an order expired by the exchange, or an order cancelled while in transit, the exchange returns `state: "cancelled"`, `state: "expired"`, or `unfilled_size < size`.
   - The FSM has **no transition** from `RECONCILING` to `CANCELLED`, `PARTIALLY_FILLED`, or `EXPIRED`.
   - Attempting to apply the exchange state raises `InvalidStateTransitionError`. Per Section D.2 line 309, `RECONCILING` "Blocks strategy from sending new orders". The strategy is permanently hung.
2. **Immediate Fill Transition Gap (`SUBMITTED` $\to$ `FILLED`):**
   - For aggressive limit orders or IOC orders crossing the book, Delta WebSocket pushes `user_trades` fill frames before the HTTP POST response completes.
   - If the router enforces `SUBMITTED -> ACCEPTED -> FILLED`, an incoming fill packet arriving while the order is in `SUBMITTED` will trigger an `InvalidStateTransitionError`. The FSM must allow direct transition `SUBMITTED -> FILLED` and `SUBMITTED -> PARTIALLY_FILLED`.

### Dimension 3: Fee Discovery, Caching & Negative Rebates
1. **Self-Contradiction on Negative Maker Rebates:**
   - Section B.1 line 77 acknowledges: "Market makers may operate under bespoke negative fee agreements (rebates)."
   - Section B.4 line 143 mandates: "Commission rates must be finite, **non-negative**, and strictly $\le 0.05$."
   - If an institutional account receives a maker rebate (e.g., $-0.0001$), `_validate_product_specification` will reject the product with `DeltaProductSpecificationError`, Gate 9 will fail-closed, and trading is halted.
2. **Stale Fee Cache Overreach:**
   - Section B.4 states that expired fee caches trigger fail-closed order blocking via Gate 9.
   - The spec does NOT distinguish between entry orders and protective exit orders. If the fee cache expires during an API network blip, an active position cannot submit synthetic stop-loss orders (`stop_market_order`) or exit orders. Gate 9 must strictly gate *entry* orders only.
3. **Hazardous Single-Fill Fee Rate Auto-Update:**
   - Section B.5 line 155 states that if $|\text{Fee}_{\text{expected}} - \text{Fee}_{\text{actual}}| > \$0.01$, the engine will "Update account fee schedule to match empirical exchange reality."
   - Discrete fill fees reflect tick rounding, contract multipliers, and minimum fee clips. Calculating a new fee rate from a single fill is mathematically flawed and risks corrupting the fee schedule. The engine must log the discrepancy and trigger a background refresh of the authoritative schedule, not mutate the schedule from fill noise.

### Dimension 4: Three-Way Reconciliation Daemon, Race Conditions & Deadlocks
1. **False-Positive Ghost Position on In-Flight & Asynchronous Fills:**
   - The continuous reconciliation cycle runs every 10 seconds.
   - When a live order fills on Delta, Delta's `/v2/positions` updates immediately.
   - If the WebSocket fill message is delayed by 500ms (due to network transit or DB transaction queuing), PostgreSQL `positions` is still 0.
   - The daemon immediately detects a quantity mismatch ($\text{Tol}_Q = 0$), declares a GHOST POSITION, halts trading, and latches the Kill Switch into `MANUAL_RESET_REQUIRED`.
   - The reconciliation daemon must verify whether any orders are currently in `SUBMITTED`, `ACCEPTED`, or `PARTIALLY_FILLED` before classifying an unexplained position as a ghost position.
2. **Operator Recovery Deadlock (The Infinite Ghost Loop):**
   - When a genuine ghost position is detected, the Kill Switch latches into `MANUAL_RESET_REQUIRED`.
   - Section F.2 provides an endpoint `/api/v1/risk/kill-switch/reset` for an operator HMAC reset.
   - If the operator resets the Kill Switch, but the ghost position remains on the exchange (or is not recorded in PostgreSQL), the reconciliation cycle runs 10 seconds later, detects the ghost position AGAIN, and re-latches the Kill Switch.
   - The spec provides **zero operator remediation workflow** (e.g. `adopt_ghost_position` into PostgreSQL or `execute_authorized_manual_flatten`).
3. **8-Hour Perpetual Funding Cash Tolerance Breach:**
   - Table C.3 defines $\text{Tol}_{\text{cash}} = \max(10 \times \text{TickValue},\ 0.0001 \times \text{Balance})$. For a $\$10,000$ balance, $\text{Tol}_{\text{cash}} = \$1.00$.
   - On Delta Exchange, perpetual contracts settle funding every 8 hours. A 10-contract position ($27,000 notional) at a 3 bps funding rate incurs an $\$8.10$ cash deduction.
   - This deduction exceeds the $\$1.00$ tolerance and triggers a reconciliation breach, halting trading every 8 hours. The tolerance formula must account for funding settlement boundaries.
4. **Unrealized PnL Tolerance Mark-Price Divergence:**
   - Table C.3 defines $\text{Tol}_{\text{UPnL}} = \text{PositionSize} \times \text{ContractValue} \times \text{Spread}$.
   - Delta computes UPnL using Mark Price (derived from an index TWAP and funding basis). Local UPnL uses Mid Price or Fill Price.
   - In volatile gold markets, $|\text{Mark Price} - \text{Mid Price}|$ frequently exceeds the instantaneous bid-ask spread, causing false reconciliation drift alarms.

### Dimension 5: Concurrency, Distributed Leases & Fencing Tokens
1. **Fencing Token Ignores Lease Expiration (`expires_at`):**
   - Section D.4 proposes fencing token verification via:
     `UPDATE positions SET quantity = :new_qty WHERE id = :pos_id AND :current_epoch = (SELECT lease_epoch FROM leader_leases WHERE lease_name = 'live_execution_leader');`
   - If Leader A's lease expires after 15 seconds, but no standby node has claimed leadership, `lease_epoch` in `leader_leases` remains unchanged.
   - Leader A waking from a 30-second network partition can continue writing to the database because `current_epoch == lease_epoch`.
   - The query MUST verify both epoch and expiration: `AND expires_at > CURRENT_TIMESTAMP`.
2. **Host Clock Skew vs Database Clock:**
   - The spec does not mandate using PostgreSQL's server clock (`clock_timestamp()`) for lease acquisition and renewal.
   - If worker nodes use local `datetime.now(timezone.utc)`, slight clock drift between containers can cause premature lease preemption or split-brain leadership.
3. **Fencing Token Incompatibility with `INSERT` Operations:**
   - Section D.4 states "Every mutating database write (INSERT, UPDATE...) must verify fencing token", but standard `INSERT INTO ... VALUES (...)` does not accept a `WHERE` clause. The spec must define how `INSERT` queries validate the lease token (e.g. `INSERT ... SELECT ... WHERE`).
4. **Fencing Tokens Cannot Fence External Delta Exchange REST Calls:**
   - A database fencing token only protects PostgreSQL rows; Delta Exchange knows nothing about PostgreSQL leases. A partitioned leader could send live orders to Delta while losing leadership. An explicit pre-flight check `is_leader_valid()` must immediately precede external HTTP dispatches.

### Dimension 6: Decision A Test Plan Gaps
1. **Testnet Liquidity Vacuum on `PAXGUSDT`:**
   - Tests 2, 3, 5, and 6 require partial fills and price movement through SL/TP levels.
   - As documented in `PROJECT_CONSTITUTION.md` Section 5.4, Delta `PAXGUSDT` suffers from low volume; on Testnet, there is virtually zero liquidity and no active market matching.
   - The test plan completely omits the requirement for a secondary Counterparty Bot / Testnet Market Maker to post bids/offers and cross trades.
2. **Synthetic Bracket OCO Companion Leg Leak:**
   - In Architecture 2 (Synthetic Stops), when Stop Loss executes, the Take Profit limit order remains resting on Delta Exchange.
   - If the strategy enters a new position shortly after, the resting TP order can fill against the new position!
   - The spec lacks an automated OCO cancellation lifecycle and reconciliation clean-up for synthetic stops.

### Dimension 7: Deterministic Risk Gate Coverage Gaps
1. **Dollar-Risk per Trade Sizing Dropped:**
   - Phase 3 Gate 3 (`max_risk_per_trade_pct = 1.0%` of equity based on $|Entry - SL|$) clamped order quantities.
   - M4.2 Gate 2 substituted a simple notional cap `MaxCapitalPerTrade`. Without dollar-risk clamping, wide-stop trades can incur catastrophic losses while staying under a notional cap.
2. **Order Rejection Burst Circuit Breaker Dropped:**
   - Design Report Gate 9 (`>= 3 rejections in 10 minutes -> Trips Kill Switch`) was dropped. Without it, algorithmic loops encountering exchange rejects can spam Delta and incur IP bans.
3. **Ambiguous Gate Definitions:**
   - Gate 4 specifies "Trailing daily loss >= 3.0% of equity". It does not specify whether this is a 24-hour rolling window or from 00:00 UTC.
   - Gate 8 specifies "Spread > 3 x TypicalSpread". "TypicalSpread" is completely undefined.

### Dimension 8: Unverified Exchange Claims & Assumptions
1. **Tier 1 Authenticated VIP Fee Endpoint Unverified:**
   - Section B.3 line 123 admits: "*The exact schema for authenticated VIP tier lookup in Delta REST API v2 must be audited against official documentation or an online Testnet probe.*"
   - Designing an architecture dependent on Tier 1 account fee discovery before the endpoint exists in `client.py` is speculative.
2. **WebSocket Sequence Numbers Assumption:**
   - Section C.6 line 258 claims private WebSocket frames have sequence markers and drops frames with older timestamps.
   - Dropping frames on `timestamp <= last_timestamp` would drop legitimate multiple fills occurring within the same millisecond.

---

## 4. DETAILED FINDINGS REGISTER & FAILURE SCENARIOS

### Finding AUD-P0-01 (P0 — Critical Blocker)
- **Title:** False-Positive Ghost Position Panic on In-Flight & Asynchronous Fills
- **Evidence:** `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` Section C.2 (lines 198-210) & Section C.3 (line 218).
- **Failure Scenario:**
  1. `LiveOrderRouter` submits an order to Delta Exchange for 5 contracts of `PAXGUSDT`.
  2. The order fills immediately on Delta's matching engine.
  3. Delta updates the account position to 5 contracts.
  4. The WebSocket `user_trades` fill message is in network transit (e.g. 250ms latency).
  5. The 10-second `ReconciliationDaemon` cycle executes at this exact millisecond.
  6. The daemon queries `/v2/positions` (sees 5 contracts) and PostgreSQL `positions` (sees 0 contracts).
  7. Position quantity tolerance is 0 (`Tol_Q = 0`). The daemon immediately flags a "Ghost Position".
  8. Section C.4 triggers: trading is frozen, Kill Switch latches to `MANUAL_RESET_REQUIRED`, and pending protective stops are blocked.
- **Potential Impact:** Regular trading is constantly halted on legitimate fills; false P0 emergency alerts are broadcast to operators.
- **Recommended Disposition:** Block Acceptance. The reconciliation cycle must maintain an in-flight order grace period (e.g., cross-referencing orders in `SUBMITTED`, `ACCEPTED`, or `PARTIALLY_FILLED` within the last 30 seconds) before declaring a position mismatch to be a Ghost Position.

### Finding AUD-P0-02 (P0 — Critical Blocker)
- **Title:** Operator Recovery Deadlock & Infinite Ghost Loop
- **Evidence:** `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` Section C.4 (lines 231-240) & Section F.2 (lines 447-452).
- **Failure Scenario:**
  1. A genuine Ghost Position (e.g. manual trade on web UI) of 2 contracts appears on Delta Exchange.
  2. The reconciliation daemon detects it, freezes trading, and latches Kill Switch to `MANUAL_RESET_REQUIRED`.
  3. Human Admin investigates and executes an authorized HMAC reset POST to `/api/v1/risk/kill-switch/reset`.
  4. The Kill Switch state resets to `NORMAL`.
  5. Exactly 10 seconds later, `ReconciliationDaemon` executes its next scheduled audit cycle.
  6. Delta Exchange still holds 2 contracts; PostgreSQL still holds 0 contracts.
  7. The daemon immediately re-trips Gate 10, freezes trading, and re-latches the Kill Switch to `MANUAL_RESET_REQUIRED`.
- **Potential Impact:** Permanent system deadlock. The operator cannot resume trading because no mechanism exists to synchronize local state with exchange reality.
- **Recommended Disposition:** Block Acceptance. M4.2 must specify operator remediation endpoints:
  - `POST /api/v1/reconciliation/adopt_ghost_position` (synchronizes PostgreSQL `positions` to match exchange).
  - `POST /api/v1/reconciliation/operator_flatten` (dispatches authorized human market order with `reduce_only: true`).
  - Kill Switch reset must verify zero outstanding reconciliation discrepancies before transitioning to `NORMAL`.

### Finding AUD-P0-03 (P0 — Critical Blocker)
- **Title:** Omission of Peak-to-Trough Portfolio Drawdown Circuit Breaker from Risk Gates
- **Evidence:** Compare `docs/PHASE_4_DESIGN_REPORT.md` Section 9 (Gate 1: Portfolio Drawdown Cap 10.0%) with `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` Section F.1 (Table F.1, lines 420-432).
- **Failure Scenario:**
  1. Account suffers a 2.5% loss on Monday (Gate 4 daily cap of 3.0% not breached).
  2. Account suffers a 2.5% loss on Tuesday (Gate 4 not breached).
  3. Account suffers a 2.5% loss on Wednesday (Gate 4 not breached).
  4. Account suffers a 2.5% loss on Thursday (Gate 4 not breached).
  5. Cumulative peak-to-trough drawdown reaches 10.0% (and proceeds to 15%, 20%).
  6. Because Gate 1 in M4.2 was replaced by a static configuration check (`LIVE_TRADING_ENABLED == False`), NO drawdown circuit breaker exists in the live risk engine.
- **Potential Impact:** Severe financial loss and catastrophic account ruin without triggering any risk halt.
- **Recommended Disposition:** Block Acceptance. Reinstate Portfolio Peak-to-Trough Drawdown Cap ($\ge 10.0\% \to \text{Kill Switch Trigger}$) as a mandatory runtime gate. Static flags (`LIVE_TRADING_ENABLED`) belong in pre-flight bootstrap, not the runtime risk gate table.

### Finding AUD-P1-01 (P1 — Major Financial / Safety Risk)
- **Title:** `RECONCILING` State Cannot Transition to `CANCELLED`, `PARTIALLY_FILLED`, or `EXPIRED`
- **Evidence:** `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` Section D.1 (lines 284-288) & Section D.2 (line 309).
- **Failure Scenario:**
  1. An order experiences a network timeout on submission (`SUBMITTED -> FAILED -> RECONCILING`).
  2. The order was an IOC limit order. On Delta Exchange, it partially filled 2 lots and cancelled the remaining 8 lots.
  3. `ReconciliationDaemon` queries `/v2/orders` and finds the order is `state: "cancelled"` with cumulative fill of 2 lots.
  4. The daemon attempts to update the order state. The FSM only permits `RECONCILING -> ACCEPTED`, `RECONCILING -> FILLED`, and `RECONCILING -> REJECTED`.
  5. The FSM raises `InvalidStateTransitionError`. The order remains stuck in `RECONCILING`.
  6. Per Section D.2 line 309, `RECONCILING` blocks the strategy from sending any new orders indefinitely.
- **Potential Impact:** Permanent execution freeze on routine IOC or partial-fill timeouts.
- **Recommended Disposition:** Block Acceptance. Add valid FSM transitions:
  - `RECONCILING -> PARTIALLY_FILLED`
  - `RECONCILING -> CANCELLED`
  - `RECONCILING -> EXPIRED`

### Finding AUD-P1-02 (P1 — Major Financial / Safety Risk)
- **Title:** Fencing Token Ignores Lease Expiration (`expires_at` Bypassed on Mutating Writes)
- **Evidence:** `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` Section D.4 (lines 330-333).
- **Failure Scenario:**
  1. Worker A acquires leadership with `lease_epoch = 10` and `expires_at = T + 15s`.
  2. Worker A experiences a 30-second GC pause or OS network stall.
  3. Worker A's lease expires at $T+15\text{s}$.
  4. No standby worker claims leadership immediately (standby is booting or absent). In `leader_leases`, `lease_epoch` is still `10`.
  5. Worker A resumes execution at $T+30\text{s}$ and executes:
     `UPDATE positions SET quantity = :new_qty WHERE id = :pos_id AND 10 = (SELECT lease_epoch FROM leader_leases WHERE lease_name = 'live_execution_leader');`
  6. The update succeeds because `10 == 10`, despite the lease being expired for 15 seconds!
- **Potential Impact:** Zombie/partitioned workers write stale or conflicting state to the durable database.
- **Recommended Disposition:** Block Acceptance. Amend SQL fencing condition to mandate expiration validation:
  `AND :current_epoch = (SELECT lease_epoch FROM leader_leases WHERE lease_name = 'live_execution_leader' AND expires_at > CURRENT_TIMESTAMP)`

### Finding AUD-P1-03 (P1 — Major Financial / Safety Risk)
- **Title:** Self-Contradiction on Negative Maker Fees (Rebates Treated as Malformed Data)
- **Evidence:** `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` Section B.1 (line 77) vs. Section B.4 (line 143).
- **Failure Scenario:**
  1. User account achieves VIP tier or market maker status with a maker fee rebate of $-0.01\%$ ($-0.0001$).
  2. Delta Exchange returns `maker_commission_rate: -0.0001`.
  3. `_validate_product_specification()` checks: `commission rates must be finite, non-negative, and strictly <= 0.05`.
  4. It raises `DeltaProductSpecificationError("Product metadata missing mandatory commission rates; fallback defaults prohibited")`.
  5. Gate 9 trips on startup; live trading is completely blocked for institutional accounts entitled to rebates.
- **Potential Impact:** Total system failure for institutional accounts operating under maker rebate agreements.
- **Recommended Disposition:** Block Acceptance. Modify fee validation bounds to:
  $-0.0010 \le \text{maker\_fee} \le 0.0500$ (permitting maker rebates up to 10 bps). Taker fees remain strictly non-negative.

### Finding AUD-P1-04 (P1 — Major Financial / Safety Risk)
- **Title:** 8-Hour Perpetual Funding Payment Breaches Cash Balance Tolerance Band
- **Evidence:** `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` Section C.3 (line 221) & `docs/PHASE_4_DESIGN_REPORT.md` Section 2 (item 4).
- **Failure Scenario:**
  1. System holds an active position of 10 contracts of `PAXGUSDT` ($27,000 notional) across 16:00 UTC.
  2. Delta Exchange charges an 8-hour funding rate of $0.03\%$ ($8.10 USDT$).
  3. Delta deducts $8.10 from account cash.
  4. Table C.3 sets $\text{Tol}_{\text{cash}} = \max(10 \times \text{TickValue},\ 0.0001 \times 10,000) = \$1.00\text{ USDT}$.
  5. At 16:00:05 UTC, `ReconciliationDaemon` executes.
  6. The cash balance discrepancy is $\$8.10 > \$1.00$.
  7. The daemon triggers a tolerance breach, trips Gate 10, freezes trading, and latches the Kill Switch.
- **Potential Impact:** False-positive trading halts every 8 hours on normal derivative cash settlements.
- **Recommended Disposition:** Block Acceptance. Implement funding event reconciliation:
  - Add $\pm \text{CumulativeFundingWindow}$ to $\text{Tol}_{\text{cash}}$ within 15 minutes of funding settlement boundaries (00:00, 08:00, 16:00 UTC).
  - Reconcile funding deductions against Delta `/v2/fills` or wallet transactions before flagging cash drift.

### Finding AUD-P1-05 (P1 — Major Financial / Safety Risk)
- **Title:** Testnet Liquidity Vacuum on `PAXGUSDT` (Tests 2, 3, 5, 6 Impracticable Without Bot)
- **Evidence:** `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` Section E.4 (lines 383-408).
- **Failure Scenario:**
  1. Operator attempts to execute the 10-Point Empirical Testnet Verification Protocol on Delta Testnet.
  2. Test 2 submits a limit entry order. Test 3 submits an order for 10 lots expecting a 4-lot partial fill.
  3. Because `PAXGUSDT` on Testnet has zero organic market makers and an empty order book, orders rest unfilled indefinitely.
  4. Tests 5 and 6 attempt to "drive market price through stop price and target price". Price never moves.
  5. Protocol hangs; Decision A cannot be empirically resolved.
- **Potential Impact:** Complete inability to validate native brackets on Testnet; blocked release of Phase 4.
- **Recommended Disposition:** Block Acceptance. The Testnet protocol specification must mandate an automated Testnet Liquidity Harness (Counterparty Bot) running on a secondary Testnet account that places opposing limit orders and crosses spreads to generate partial fills and price sweeps.

### Finding AUD-P1-06 (P1 — Major Financial / Safety Risk)
- **Title:** Synthetic Bracket OCO Companion Leg Leak (Old TP Fills Against New Position)
- **Evidence:** `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` Section E.3 (lines 373-377).
- **Failure Scenario:**
  1. System enters long position of 10 contracts at $2,700 under Architecture 2.
  2. Router places Synthetic Take Profit (Limit Sell at $2,750, `reduce_only: true`) and Stop Loss (Stop Market at $2,650, `reduce_only: true`).
  3. Price drops to $2,650; Stop Loss executes, flattening the position to 0.
  4. Delta Exchange matching engine does NOT automatically cancel the resting TP limit order at $2,750.
  5. Router fails to cancel TP (e.g. network hiccup or crash).
  6. 30 minutes later, strategy generates a new LONG signal for 10 contracts at $2,710.
  7. Position is now 10 long. Price spikes to $2,750.
  8. The OLD orphaned TP order executes against the NEW position, closing it prematurely!
- **Potential Impact:** Unintended order executions, orphaned resting orders, and corrupted strategy state.
- **Recommended Disposition:** Block Acceptance. Architecture 2 must specify an explicit synthetic OCO coordinator that cancels the companion order upon fill confirmation, and mandates that the reconciliation daemon cancels any resting `reduce_only` orders when position size is 0.

### Finding AUD-P1-07 (P1 — Major Financial / Safety Risk)
- **Title:** Stale Fee Cache Overreach Blocks Protective Stops and Emergency Exits
- **Evidence:** `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` Section B.4 (lines 145-147) & Section F.1 (Gate 9, line 430).
- **Failure Scenario:**
  1. An open position of 10 contracts exists.
  2. The 24-hour fee cache expires. A background HTTP request to refresh fees fails due to transient exchange downtime (HTTP 502/504).
  3. Gate 9 trips: "Fees or contract geometry unverified -> Fail-closed; blocks entry."
  4. If Gate 9 is checked globally in `order_router.py` on all orders, it blocks the dispatch of stop-loss orders or emergency exit orders.
  5. An open position is left floating without stops during exchange API instability.
- **Potential Impact:** Inability to exit losing positions or place protective stops during API degradation.
- **Recommended Disposition:** Block Acceptance. Explicitly specify that Gate 9 applies exclusively to NEW POSITION ENTRIES. Exits, protective stops, and emergency liquidations must bypass Gate 9.

### Finding AUD-P1-08 (P1 — Major Financial / Safety Risk)
- **Title:** Omission of Dollar-Risk Sizing Clamping and Order Rejection Burst Gate
- **Evidence:** Compare `src/risk/paper_risk_engine.py` (Gate 3) and `docs/PHASE_4_DESIGN_REPORT.md` (Gate 3 & Gate 9) with `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` Section F.1.
- **Failure Scenario:**
  1. Strategy generates a signal with an entry price of $2,700 and a stop loss of $2,430 (10% wide stop).
  2. M4.2 Gate 2 only checks `Position notional > MaxCapitalPerTrade`. If $5,000 notional is requested, Gate 2 approves it.
  3. If stopped out, the trade loses $500 (5.0% of a $10,000 account), violating the institutional 1.0% risk rule.
  4. In addition, if Delta rejects an order repeatedly, no gate halts execution, risking an infinite dispatch loop and exchange rate-limit bans.
- **Potential Impact:** Sizing violations causing oversized capital loss; risk of API bans on rejection bursts.
- **Recommended Disposition:** Block Acceptance. Restore:
  - Gate 3: $\text{DollarRisk} = |Entry - SL| \times Qty \times Multiplier \le 1.0\% \times Equity$ with automatic quantity clamping.
  - Gate 9: Burst Rejection Circuit Breaker ($\ge 3$ rejections in 10 minutes $\to$ Kill Switch Trigger).

---

## 5. MATERIAL ARCHITECTURAL GAPS (P2 & P3)

### AUD-P2-01 (P2): Kill Switch Architecture Conflict
- **Issue:** M4.2 replaces the frozen Phase 3 5-state Kill Switch FSM with a 4-state version without `POSITIONS_FLATTENED`.
- **Disposition:** Maintain Phase 3 `src/risk/kill_switch.py` intact. Create `src/risk/live_kill_switch.py` containing a `LiveKillSwitchFSM` that distinguishes between Ghost Exposure (freeze without market orders) and Emergency Flattening (authorized reduce-only market exit).

### AUD-P2-02 (P2): Distributed Lease Host Clock Skew
- **Issue:** Using worker host time for lease acquisition/renewal can cause split-brain leadership under clock drift.
- **Disposition:** Mandate that all lease renewal SQL queries use PostgreSQL's `CURRENT_TIMESTAMP` / `clock_timestamp()`.

### AUD-P2-03 (P2): Fencing Token SQL Syntax Incompatible with `INSERT`
- **Issue:** Proposed fencing SQL check only works on `UPDATE`, not `INSERT`.
- **Disposition:** Define the exact SQL syntax for fenced inserts:
  `INSERT INTO live_orders (...) SELECT ... WHERE :current_epoch = (SELECT lease_epoch FROM leader_leases WHERE lease_name = 'live_execution_leader' AND expires_at > CURRENT_TIMESTAMP);`

### AUD-P2-04 (P2): Database Schema Friction in `Position.account_id`
- **Issue:** Existing `positions` table has a foreign key to `paper_accounts.id`. Live positions will fail foreign key constraints.
- **Disposition:** Specify Alembic migration to drop the strict foreign key or create `live_accounts` and link polymorphically.

### AUD-P2-05 (P2): Unrealized PnL Tolerance Mark-Price Divergence
- **Issue:** $\text{Tol}_{\text{UPnL}} = \text{PositionSize} \times \text{ContractValue} \times \text{Spread}$ assumes Mark Price is within 1 spread of Mid Price.
- **Disposition:** Calibrate $\text{Tol}_{\text{UPnL}}$ to include basis drift: $\text{Tol}_{\text{UPnL}} = \text{PositionSize} \times \text{ContractValue} \times \max(\text{Spread},\ 3 \times \text{BasisTWAP})$.

### AUD-P2-06 (P2): Hazardous Single-Fill Fee Rate Auto-Update
- **Issue:** Updating stored account fee schedules from a single fill discrepancy will corrupt fee metadata.
- **Disposition:** Record the actual fee on `live_trades.fees`, emit `FEE_RECONCILIATION_DISCREPANCY`, and schedule an authoritative REST profile refresh.

### AUD-P3-01 & AUD-P3-02 (P3): Documentation Cleanup
- Unify table naming to `account_fee_schedules`.
- Add `CREATED -> REJECTED` transition for pre-route HMAC signing or validation errors.

---

## 6. REMEDIATION ROADMAP FOR ACCEPTANCE

To achieve `DESIGN AUDIT PASSED`, the following remediations must be incorporated into a revised `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md`:

1. **Reconcile Risk Gates to 12 Mandatory Live Gates:**
   - Reinstate Portfolio Drawdown Cap ($\ge 10.0\% \to \text{Kill Switch Trigger}$).
   - Reinstate Dollar-Risk Per Trade Clamping ($\le 1.0\% \text{ Equity}$).
   - Reinstate Order Rejection Burst Gate ($\ge 3 \text{ rejects in 10m} \to \text{Kill Switch Trigger}$).
   - Reinstate Market Regime Filter.
   - Retain Single Strategy Lock, Feed Latency, Clock Drift, Spread Ceiling, Fee Completeness, and Three-Way Desync.
   - Move `LIVE_TRADING_ENABLED` to pre-flight startup assertions.
2. **Harden Three-Way Reconciliation Daemon:**
   - Add a 30-second in-flight order correlation buffer to prevent false-positive Ghost Position alerts on lagging WebSocket fills.
   - Specify operator remediation endpoints: `adopt_ghost_position` and `operator_flatten`.
   - Add 8-hour funding payment reconciliation windows to the wallet cash tolerance.
3. **Complete 11-State FSM Transitions:**
   - Add transitions: `RECONCILING -> PARTIALLY_FILLED`, `RECONCILING -> CANCELLED`, `RECONCILING -> EXPIRED`.
   - Add transitions: `SUBMITTED -> FILLED`, `SUBMITTED -> PARTIALLY_FILLED`.
4. **Harden Distributed Concurrency & Leases:**
   - Amend fencing token queries to strictly verify `expires_at > CURRENT_TIMESTAMP`.
   - Mandate PostgreSQL server clock for all lease evaluations.
   - Specify fenced `INSERT` syntax and pre-flight exchange dispatch lease checks.
5. **Remediate Fee Model & Negative Rebates:**
   - Permit negative maker rates ($-0.0010 \le \text{rate} \le 0.0500$) for VIP/MM tiers.
   - Restrict Gate 9 fail-closed blocking strictly to NEW entries.
   - Eliminate single-fill auto-updates to stored fee schedules.
6. **Harden Decision A Testnet Protocol:**
   - Mandate a secondary Testnet Counterparty Bot / Market Maker harness to supply book depth and execute cross trades.
   - Specify synthetic OCO companion order cancellation lifecycle.
7. **Preserve Frozen Phase 3 Codebase:**
   - Specify `LiveKillSwitchFSM` in `src/risk/live_kill_switch.py` to prevent modifying frozen `src/risk/kill_switch.py`.

---

## 7. FINAL AUDIT SIGN-OFF

**Official Recommendation:**  
**`DESIGN REWORK REQUIRED — REMEDIATION NEEDED`**  
The Milestone 4.2 Design Specification cannot be approved in its current state. Implementation planning must be held until the identified P0 and P1 vulnerabilities are fully remediated in a revised design document.

**Signed,**  
*Independent Senior Quant Systems Auditor & Financial Risk Officer*  
*October 9, 2026*
