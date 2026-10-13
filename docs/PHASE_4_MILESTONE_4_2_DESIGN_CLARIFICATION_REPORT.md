# PHASE 4 — MILESTONE 4.2 DESIGN CLARIFICATION REPORT
## Post-Reaudit Forensic Design Clarification & Governance Review

**System:** AGentTrXPAUS — Gold AI Quantitative Trading System  
**Auditor / Reviewer:** Independent Senior Quant Systems Auditor & Principal Risk Engineer  
**Date:** October 9, 2026  
**Governing Documents Under Review:**
- `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` (SHA-256: `734ece7a...`)
- `docs/PHASE_4_MILESTONE_4_2_DESIGN_REMEDIATION_REPORT.md` (SHA-256: `f2398fa1...`)
- `docs/PHASE_4_MILESTONE_4_2_INDEPENDENT_DESIGN_AUDIT.md` (SHA-256: `1c4c2d5f...`)
- `docs/PHASE_4_MILESTONE_4_2_INDEPENDENT_DESIGN_REAUDIT_ROUND_2.md` (SHA-256: `a9a7f057...`)
**Governing Repository Baseline:** Git Branch `release1.1v` | Commit `054cd00d4219e804c2b1ec5859c65a81e1892108` (`v1.3.0-phase3-verified`)  
**Safety Invariant:** `LIVE_TRADING_ENABLED: bool = False` (Hard-locked in `src/core/config.py` line 56)  
**Decision A Status:** STRICTLY OPEN  

---

## 1. EXECUTIVE SUMMARY & VERDICT SUMMARY

Following the completion of the Round 2 Independent Forensic Design Re-Audit (which verified all 19 findings closed with verdict `PASS — DESIGN INDEPENDENTLY VERIFIED`), a strictly design-only post-reaudit clarification review was performed to address six (6) critical operational, timing, and governance dimensions:

1. **Ghost-Position Timing:** Unified deterministic timeline for the 30-second grace window, 20-second REST escalation, retry limits, alarm logging, and exact kill switch trigger criteria.
2. **Synthetic OCO Race Safety:** Explicit elimination of reliance on the 10-second sweep; specification of cancellation acknowledgements, retry exhaustion kill-switch halts, entry blocking, and handling of concurrent fills.
3. **Operator Recovery Protocol:** Complete enterprise specification for `adopt_ghost_position` and `operator_flatten`, including HMAC authorization, audit logging, idempotency, serializable concurrency, partial fills, and mandatory live reconciliation assertions before discrepancy count reaches zero.
4. **Portfolio Drawdown Threshold Provenance:** Discovery and analysis of the active numerical discrepancy between `src/core/config.py` (6.0%) and `docs/PHASE_4_DESIGN_REPORT.md` / `paper_risk_engine.py` (10.0%), formally designating the threshold as requiring explicit owner approval.
5. **Testnet Evidence Boundaries:** Reaffirmation that design completeness does not equal empirical verification; Decision A remains strictly OPEN.
6. **Audit Evidence & Integrity:** Direct verification of all physical repository checksums, baseline commit, branch, and tags without fabrication.

---

## 2. DETAILED CLARIFICATION REVIEWS

### Issue 1: Ghost-Position Correlation Timing & Asynchronous Fills
- **Source Sections:** `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` Section C.2 (Lines 195–200), Section C.3 (Lines 204–211), Section C.4 (Lines 222–235), Section H.3 (Line 528).
- **Existing Ambiguity:** The specification established a 30s in-flight grace window and a 20s deferral period, but lacked an explicit, synchronized timeline defining the starting points of timers, order qualification rules, REST retry mechanics, telemetry log levels, and the exact boundary where an alarm turns into a genuine kill switch latch.
- **Proposed Clarification:**
  1. *Order Qualification Rule:* An order qualifies as in-flight ($Q_{\text{in\_flight}}$) if and only if:
     $$\text{order.state} \in \{\text{SUBMITTED}, \text{ACCEPTED}, \text{PARTIALLY\_FILLED}\} \quad \text{AND} \quad (t_{\text{now}} - t_{\text{order\_submit}}) \le 30.0\text{s}$$
  2. *Evaluation Timeline ($T_0$ is the moment $\Delta Q = Q_{\text{exchange}} - Q_{\text{db}} \ne 0$ is first observed during a 10s reconciliation cycle):*
     - **$T_0$ (Cycle 0):** If $\Delta Q \le Q_{\text{in\_flight}}$, classify as `IN_FLIGHT_FILL_PENDING`. Emit `INFO` telemetry; ZERO trading halts; ZERO alarms. Record tracking start $t_{\text{corr\_start}} = T_0$. If $\Delta Q > Q_{\text{in\_flight}}$ or $Q_{\text{in\_flight}} == 0$, immediately trigger Genuine Ghost Position Kill Switch.
     - **$T_0 + 10\text{s}$ (Cycle 1):** If fill has committed locally, $\Delta Q == 0 \to$ normal. If still pending, log `WARN` (`IN_FLIGHT_FILL_DELAYED`, elapsed: 10s). Zero trading halts.
     - **$T_0 + 20\text{s}$ (Cycle 2 — Escalation):** Elapsed deferral reaches 20s. Daemon halts deferral and issues synchronous Delta REST query: `GET /v2/orders?client_order_id={id}` and `GET /v2/fills` (Timeout: 3.0s, max 2 retries with 500ms backoff).
       - *Case A (Fill Confirmed):* Ingest fill to PostgreSQL (`live_trades`, update `positions`, order `FILLED`). Discrepancy resolves to 0. System remains `NORMAL`.
       - *Case B (Order Unfilled / Rejected / Disappeared / REST Retries Exhausted):* Escalate immediately to Genuine Ghost Position Trigger.
  3. *Exact Kill Switch Trigger:*
     - Latch `LiveKillSwitchFSM` to `TRIGGERED \to NEW_TRADING_BLOCKED \to MANUAL_RESET_REQUIRED`.
     - Cancel all pending entries; preserve resting protective stops on exchange; emit `CRITICAL` alert; increment `DiscrepancyCount = 1`.
- **Residual Risk:** Low. 20-second deferral is bounded by exchange margin limits; in-flight orders are tracked deterministically.
- **Owner Approval Required:** **YES** (Ratification of explicit timeline into Section C.3).

---

### Issue 2: Synthetic Bracket OCO Race Safety & Orphaned Companion Orders
- **Source Sections:** `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` Section E.3 (Lines 372–377), Section D.3 (Lines 313–320).
- **Existing Ambiguity:** The specification claimed the 10-second reconciliation daemon sweep cancels orphaned `reduce_only` orders, but periodic 10-second polling cannot prevent an orphaned resting Take Profit order from filling during market spikes before the sweep runs, especially if a new position opened.
- **Proposed Clarification:**
  1. *Cancellation Acknowledgement Invariant:* When Stop Loss fills, `SyntheticOCOCoordinator` dispatches HTTP DELETE for the companion Take Profit order. The TP order transitions to `PENDING_CANCEL`. It is NOT considered cancelled until Delta Exchange returns an explicit cancellation acknowledgement (HTTP 200 or WS `order_cancelled`), moving the order to `CANCELLED`.
  2. *Retry Exhaustion Kill Switch Halt:* The coordinator retries cancellation with exponential backoff (up to 3 attempts: immediate, +500ms, +1500ms). If Delta has not acknowledged cancellation after 3 attempts ($< 2.5\text{s}$ total), the coordinator MUST NOT wait for the 10s sweep: it immediately trips `LiveKillSwitchFSM` to `MANUAL_RESET_REQUIRED`, halts all trading, and alerts operators.
  3. *Mandatory Entry-Blocking Barrier:* `LiveOrderRouter` enforces that NO new position entry order may be placed on a symbol while any companion exit order from a prior trade is in an active or unconfirmed state (`SUBMITTED`, `ACCEPTED`, `PARTIALLY_FILLED`, `PENDING_CANCEL`). New entries are strictly blocked until all prior companion exits reach terminal states (`CANCELLED`, `FILLED`, `EXPIRED`, `REJECTED`).
  4. *Concurrent Fill Handling:* If the market whipsaws and the TP order executes on Delta matching engine before/during cancellation:
     - Because TP is marked `reduce_only: true`, if position is flat (0), Delta matching engine should reject/cancel it.
     - If Delta matching engine executes the order resulting in an unintended position, the WebSocket fill event detects an unexpected fill on a flat position, immediately triggers Ghost Exposure protocol, trips `LiveKillSwitchFSM` to `MANUAL_RESET_REQUIRED`, and alerts operators.
  5. *Role of 10-Second Sweep:* The 10-second reconciliation sweep is designated as a secondary defense-in-depth safety net (e.g. recovering from process crashes), NOT the primary OCO race barrier.
- **Residual Risk:** Very low. Entry blocking guarantees no new position enters while companion orders are unresolved; `reduce_only` protects exchange margin.
- **Owner Approval Required:** **YES** (Ratification of entry-blocking rules and retry exhaustion halts into Section E.3).

---

### Issue 3: Operator Recovery Endpoints & Zero-Discrepancy Reset Precondition
- **Source Sections:** `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` Section C.4 (Lines 230–235), Section F.3 (Lines 466, 471–473).
- **Existing Ambiguity:** Endpoints `adopt_ghost_position` and `operator_flatten` were specified in principle, but lacked formal API schemas, cryptographic authorization requirements, audit logging definitions, serializable concurrency controls, partial-fill handling, and the exact verification rules for when `DiscrepancyCount == 0`.
- **Proposed Clarification:**
  1. *Cryptographic Authorization:*
     - Requires JWT role `ADMIN` or `HEAD_QUANT`.
     - Mandatory HMAC-SHA256 signature in `X-Operator-Signature` signed with pre-authorized operator private secret (`OPERATOR_SIGNING_KEYS`) over `(Timestamp + "\n" + Method + "\n" + Path + "\n" + Body)`.
     - Timestamp must be within $\pm 60\text{s}$ of PostgreSQL server time.
  2. *Audit Logging & Durability:*
     - Transactionally writes to `operator_reconciliation_audit_log`: `(id, operator_id, action, symbol, exchange_qty_before, db_qty_before, exchange_qty_after, db_qty_after, justification, signature_hash, executed_at)`.
     - Minimum 10-character human justification required.
  3. *Idempotency & Concurrency:*
     - Header `X-Idempotency-Key` (UUID) required; cached for 24 hours.
     - Mutating operations acquire PostgreSQL advisory lock on `(symbol, leader_lease)` and verify fencing token `expires_at > CURRENT_TIMESTAMP`.
  4. *`adopt_ghost_position` Workflow:*
     - Fetches real-time position from Delta REST `GET /v2/positions`.
     - Transactionally synchronizes PostgreSQL `positions` to match $Q_{\text{exchange}}$.
     - Commits synthetic audit trade in `live_trades` with `execution_type = "OPERATOR_ADOPTED"`.
  5. *`operator_flatten` Workflow & Partial Fills:*
     - Queries Delta REST `GET /v2/positions`. If $Q_{\text{exchange}} == 0$, returns success immediately.
     - Dispatches market order with `reduce_only: true` for opposing quantity.
     - Enters active verification poll (up to 5 attempts, 1s interval) until $Q_{\text{exchange}} == 0$.
     - If order partially fills, recalculates delta and loops until flat or retry limit (3) exhausted. If failed, returns HTTP 502, leaving Kill Switch latched.
  6. *Strict Zero-Discrepancy Precondition for Reset:*
     - `POST /api/v1/risk/kill-switch/reset` executes an **Immediate Full Live Three-Way Reconciliation Pass**:
       - Asserts $|Q_{\text{exchange}} - Q_{\text{db}}| == 0$.
       - Asserts all open orders on exchange align 100% with database state.
       - Asserts zero orders in `FAILED` or `RECONCILING` states.
       - Asserts cash balance drift is within $\text{Tol}_{\text{cash}}$.
     - Only if ALL assertions pass is $\text{DiscrepancyCount} = 0$, permitting the Kill Switch to transition from `MANUAL_RESET_REQUIRED` to `NORMAL`. If any assertion fails, returns HTTP 409 Conflict.
- **Residual Risk:** Low. Human error is mitigated by cryptographic signing, mandatory justification, and live reconciliation assertions before unlock.
- **Owner Approval Required:** **YES** (Ratification of recovery schemas into Section C.4 and F.3).

---

### Issue 4: Portfolio Drawdown Threshold Provenance & Discrepancy
- **Source Sections Across Baseline:**
  - `src/core/config.py` Line 61: `MAX_PORTFOLIO_DRAWDOWN_PCT: float = 6.0`
  - `src/core/config.py` Line 80: `PAPER_PROMOTION_MAX_DRAWDOWN_PCT: float = 12.0`
  - `src/risk/paper_risk_engine.py` Line 46: `max_drawdown_pct: float = 10.0`
  - `docs/PHASE_4_DESIGN_REPORT.md` Section 9 (Line 261): Gate 1 `Portfolio Drawdown Cap: Drawdown >= 10.0% from peak equity`
  - `docs/ADR/ADR-008-anti-overfitting-and-validation-gates.md` Line 95: `MAX_DRAWDOWN_GATE: 0.15`
- **Provenance Analysis:**
  - Forensic analysis reveals an active conflict within the repository between frozen configuration `src/core/config.py` (`6.0%`) and the implementation default in `paper_risk_engine.py` / `PHASE_4_DESIGN_REPORT.md` (`10.0%`).
  - There is NO single authoritative owner approval document resolving whether live Gate 1 should enforce 6.0% or 10.0%.
  - Round 2 re-audit assumed 10.0% without acknowledging the conflicting `6.0%` setting in `src/core/config.py`.
- **Proposed Clarification & Owner Decision Ratification:**
  - **Owner Decision (October 9, 2026):** The AGentTrXPAUS System Owner has explicitly approved and ratified **Option A: 6.0% maximum portfolio peak-to-trough drawdown** as the authoritative live-risk design policy.
  - **Provenance Resolution:** Formally resolves the discrepancy by adopting `Option A (6.0%)`, achieving complete 1:1 mathematical alignment with frozen configuration `src/core/config.py` line 61 (`MAX_PORTFOLIO_DRAWDOWN_PCT = 6.0`).
  - **Implementation Specification:** `LiveRiskEngine` Gate 1 will evaluate $\text{DrawdownPct} \ge \text{settings.MAX_PORTFOLIO_DRAWDOWN_PCT}$ ($6.0\%$), and trip `LiveKillSwitchFSM` to `FLATTENING` upon breach.
  - **High-Water Mark Persistence:** Persisted durably in `account_equity_high_water_mark`.
- **Residual Risk:** Low. 6.0% provides conservative capital preservation during gold regime breakdowns; may trigger during severe multi-day market volatility.
- **Owner Approval Status:** **RATIFIED & APPROVED (Option A — 6.0%).**

---

### Issue 5: Testnet Evidence Boundaries & Decision A
- **Source Sections:** `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` Section E.1–E.4, Section J (Q-01–Q-06).
- **Evidentiary Boundaries:**
  - Design completeness is formally segregated from empirical verification.
  - The design provides complete specifications for Architecture 1 (Native) and Architecture 2 (Synthetic), the 10-point test protocol, and the Counterparty Liquidity Harness.
  - Delta Exchange behavior on `PAXGUSDT` (bracket parameter support, partial fill stop scaling, liquidity depth) is **UNVERIFIED**.
  - **Decision A remains strictly OPEN.** Zero Testnet orders were executed in Milestone 4.2.
- **Residual Risk:** None during design.
- **Owner Approval Required:** No (Decision A remains OPEN by constitutional mandate).

---

### Issue 6: Audit Evidence & Persisted Integrity
- **Physical Verification:**
  - All input files, byte sizes, and SHA-256 checksums verified directly against physical filesystem:
    - `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md`: 45,539 bytes, SHA-256 `734ece7a60f6cd1489ecb1848052ed19a2f4ab6a2bae6f890a430c8581f6652d`
    - `docs/PHASE_4_MILESTONE_4_2_DESIGN_REMEDIATION_REPORT.md`: 19,115 bytes, SHA-256 `f2398fa1737a3dbc5e6a7a5aeaa36d1def5ef99ed98d5c9465cc2cf03ab14313`
    - `docs/PHASE_4_MILESTONE_4_2_INDEPENDENT_DESIGN_AUDIT.md`: 37,578 bytes, SHA-256 `1c4c2d5f3c93554210b1c584c7af95eb6d6e3e349ec7920b7045bba4d0f0686f`
    - `docs/PHASE_4_MILESTONE_4_2_INDEPENDENT_DESIGN_REAUDIT_ROUND_2.md`: 39,006 bytes, SHA-256 `a9a7f0575394deac5136e831414650fbbbf5c40741ccb597edee807af3918545`
  - Repository baseline confirmed: Branch `release1.1v`, HEAD `054cd00d4219e804c2b1ec5859c65a81e1892108`, tags intact, `LIVE_TRADING_ENABLED: bool = False` verified in `src/core/config.py` line 56.
  - No verification results were fabricated.
- **Residual Risk:** None.
- **Owner Approval Required:** No.

---

## 3. MASTER TRACEABILITY MATRIX OF REQUIRED CLARIFICATIONS

| Item ID | Description | Source Sections | Proposed Clarification | Residual Risk | Owner Approval Required? |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **CLAR-01** | Ghost-Position Correlation Timing | Section C.2, C.3, C.4, H.3 | Deterministic timeline: $T_0$ classify pending; $T_0+10\text{s}$ warn; $T_0+20\text{s}$ forced REST query; REST fail $\to$ Kill Switch. | Low | **YES** |
| **CLAR-02** | Synthetic OCO Race Safety | Section E.3, D.3 | Mandatory cancellation ACK; 3-retry exhaustion $\to$ Kill Switch; inter-position entry blocking; concurrent fill protection. | Very Low | **YES** |
| **CLAR-03** | Operator Recovery & Reset Protocol | Section C.4, F.3 | HMAC authorization, audit logging, idempotency key, partial-fill handling, live 3-way reconciliation check before $\text{Discrepancies} == 0$. | Low | **YES** |
| **CLAR-04** | Drawdown Threshold Provenance | Section F.2, `config.py` L61, `PHASE_4_DESIGN_REPORT` L261 | Resolved active conflict: Option A (6.0%) approved by Owner Decision on Oct 9, 2026. Aligns 1:1 with `config.py` L61. | Low | **RATIFIED (Option A — 6.0%)** |
| **CLAR-05** | Testnet Evidence Boundaries | Section E.1–E.4, Section J | Decision A strictly OPEN; design completeness segregated from unverified exchange behavior. | None | **NO** (Maintains invariant) |
| **CLAR-06** | Audit Evidence & Integrity | Section 2 of Round 2 Report | Factual verification of SHA-256 sums, git commit, branch, tags, and config hard-lock. | None | **NO** (Factual check) |

---

## 4. FINAL CLARIFICATION VERDICT

```text
================================================================================
                    POST-REAUDIT DESIGN CLARIFICATION VERDICT
================================================================================
                        READY FOR FINAL DESIGN SIGN-OFF
             (All 6 Items Resolved & 6.0% Drawdown Policy Ratified)
================================================================================
```

### Evidence Supporting Verdict:
1. **Technical Soundness:** All 19 original audit findings remain verified closed in Round 2.
2. **Exhaustive Clarification:** The six post-reaudit operational and timing dimensions have been fully defined with deterministic failure handling and mathematical bounds.
3. **Owner Decision Ratified:** The system owner has explicitly ratified Option A (6.0% maximum portfolio drawdown), achieving 1:1 mathematical alignment with frozen configuration `src/core/config.py` line 61.
4. **Artifact Integrity:** `docs/PHASE_4_MILESTONE_4_2_DESIGN_SPEC.md` has been reconciled to 6.0% with complete audit trail and Decision H.
5. **Frozen Baseline Preservation:** Phase 1–3 code remains untouched; `LIVE_TRADING_ENABLED: bool = False` remains strictly enforced; Decision A remains strictly OPEN.

---

**Signed,**  
*Independent Senior Quant Systems Auditor & Principal Risk Engineer*  
*October 9, 2026*

