"""Three-Way Continuous Reconciliation Daemon.

Reconciles state across:
1. In-Memory Trading Engine State (Order records, active exits, FSM)
2. Authoritative PostgreSQL Database (live_orders, positions, live_accounts)
3. Delta Exchange Matching Engine REST API (/v2/orders, /v2/positions, /v2/wallet/balances)

Equipped with:
- 30-second in-flight order correlation buffer (T_grace = 30s)
- Discrete zero-tolerance position quantity checks
- Dynamic funding-aware cash tolerances (+/- 15m window around 00:00, 08:00, 16:00 UTC)
- Basis-aware UPnL tolerance calculation
- Ghost position detection and fail-closed procedure (ZERO automated liquidation)
- Dynamic bracket stop-loss alignment verification
- Periodic sweep of orphaned synthetic OCO companion orders
- Integration with LeaderLeaseManager and LiveOrderRouter
"""

import asyncio
from datetime import datetime, timezone, timedelta
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.database.models.instruments import Instrument
from src.database.models.live import (
    LiveAccount,
    LiveFundingEvent,
    LiveOrder,
    LiveOrderEvent,
    ReconciliationSnapshot,
    OperatorReconciliationAuditLog,
)
from src.database.models.trading import Position
from src.execution.delta.client import DeltaRestClient
from src.execution.delta.fsm import OrderEvent, OrderState
from src.execution.delta.order_router import LiveOrderRouter
from src.execution.delta.types import (
    DeltaConnectorError,
    DeltaNetworkError,
    DeltaOrderSide,
    DeltaOrderType,
    DeltaTimeInForce,
)
from src.execution.leadership import LeaderLeaseManager

logger = logging.getLogger("gold_quant.reconciliation")


def is_in_funding_settlement_window(dt: Optional[datetime] = None) -> bool:
    """Determine whether a datetime is within +/- 15 minutes of UTC funding times.
    
    Delta Exchange perpetual contracts settle funding every 8 hours at:
    00:00:00 UTC, 08:00:00 UTC, 16:00:00 UTC.
    Window bounds:
    - 23:45:00 to 00:15:00 UTC (wraparound 00:00)
    - 07:45:00 to 08:15:00 UTC (08:00)
    - 15:45:00 to 16:15:00 UTC (16:00)
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    seconds = dt.hour * 3600 + dt.minute * 60 + dt.second
    # Boundaries in seconds from midnight: 0 (00:00), 28800 (08:00), 57600 (16:00), 86400 (24:00)
    for target in (0, 28800, 57600, 86400):
        if abs(seconds - target) <= 900:  # 15 minutes = 900 seconds
            return True
    return False


class ReconciliationDaemon:
    """Automated Three-Way Continuous Reconciliation Daemon."""

    def __init__(
        self,
        client: DeltaRestClient,
        session_factory: async_sessionmaker[AsyncSession],
        order_router: Optional[LiveOrderRouter] = None,
        lease_manager: Optional[LeaderLeaseManager] = None,
        account_id: str = "ACC_LIVE_PRIMARY",
        product_id: int = 1234,
        product_symbol: str = "PAXGUSDT",
        in_flight_grace_period_seconds: float = 30.0,
        reconciliation_interval_seconds: float = 10.0,
        funding_tolerance_usd: float = 5.0,
        contract_value: float = 1.0,
        current_spread_usd: float = 0.50,
        basis_twap_usd: float = 0.50,
    ):
        self.client = client
        self.session_factory = session_factory
        self.order_router = order_router
        self.lease_manager = lease_manager
        self.account_id = account_id
        self.product_id = product_id
        self.product_symbol = product_symbol
        self.in_flight_grace_period_seconds = in_flight_grace_period_seconds
        self.reconciliation_interval_seconds = reconciliation_interval_seconds
        self.funding_tolerance_usd = funding_tolerance_usd
        self.contract_value = contract_value
        self.current_spread_usd = current_spread_usd
        self.basis_twap_usd = basis_twap_usd

        self.discrepancy_count: int = 0
        self.consecutive_transport_failures: int = 0
        self.ghost_positions_detected: List[Dict[str, Any]] = []
        self.last_snapshot_id: Optional[str] = None
        self._is_running: bool = False
        self._daemon_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def run_reconciliation_cycle(self) -> Dict[str, Any]:
        """Execute a single deterministic three-way reconciliation pass.
        
        Returns:
            Dict[str, Any]: Audit summary including discrepancy count and status.
        """
        async with self._lock:
            # Step 1: Pre-flight leadership verification
            if self.lease_manager is not None:
                is_leader = await self.lease_manager.is_leader_valid()
                if not is_leader:
                    logger.warning("Reconciliation cycle skipped: node does not hold valid leadership lease")
                    return {
                        "status": "SKIPPED_NOT_LEADER",
                        "discrepancy_count": self.discrepancy_count,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

            # Step 2: Query Delta Exchange state
            try:
                ex_orders_raw = await self.client.get_orders(state="open")
                ex_positions_raw = await self.client.get_positions()
                ex_balances_raw = await self.client.get_balances()
                self.consecutive_transport_failures = 0
            except Exception as exc:
                self.consecutive_transport_failures += 1
                logger.error(
                    f"Exchange communication failure during reconciliation (attempt {self.consecutive_transport_failures}): {exc}"
                )
                if self.consecutive_transport_failures >= 3:
                    logger.critical("TRANSPORT OUTAGE: >= 3 consecutive exchange query failures. Trading locked.")
                    if self.order_router is not None:
                        self.order_router.kill_switch_state = "MANUAL_RESET_REQUIRED"
                        self.order_router.discrepancy_count = max(self.order_router.discrepancy_count, 1)
                return {
                    "status": "EXCHANGE_TRANSPORT_OUTAGE",
                    "error": str(exc),
                    "discrepancy_count": self.discrepancy_count,
                    "consecutive_failures": self.consecutive_transport_failures,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

            now_utc = datetime.now(timezone.utc)
            discrepancies: List[str] = []

            # Parse exchange position for target product (signed quantity)
            ex_signed_size = 0.0
            ex_upnl = 0.0
            for p in ex_positions_raw:
                p_id = p.get("product_id")
                if p_id == self.product_id or str(p_id) == str(self.product_id):
                    raw_sz = float(p.get("size", 0.0))
                    side = str(p.get("side", "")).lower()
                    if side == "sell" and raw_sz > 0:
                        ex_signed_size = -raw_sz
                    else:
                        ex_signed_size = raw_sz
                    ex_upnl = float(p.get("unrealized_pnl", 0.0))
                    break

            # Parse exchange wallet cash balance
            ex_cash = 0.0
            for b in ex_balances_raw:
                # Delta balances schema: balance or available_balance or cash_balance
                if b.get("asset_symbol") in ("USDT", "USD") or str(b.get("asset_id")) in ("1", "USDT"):
                    try:
                        ex_cash = float(b.get("cash_balance", b.get("available_balance", b.get("balance", 0.0))))
                    except (ValueError, TypeError):
                        pass
                    break

            # Step 3: Query authoritative PostgreSQL database
            async with self.session_factory() as session:
                # 3a. Active orders in DB
                active_states = [
                    OrderState.SUBMITTED.value,
                    OrderState.ACCEPTED.value,
                    OrderState.PARTIALLY_FILLED.value,
                    OrderState.RECONCILING.value,
                ]
                order_stmt = select(LiveOrder).where(
                    LiveOrder.account_id == self.account_id,
                    LiveOrder.state.in_(active_states),
                )
                order_res = await session.execute(order_stmt)
                db_active_orders = order_res.scalars().all()

                # 3b. Local position in DB (signed quantity)
                pos_stmt = select(Position).where(
                    Position.live_account_id == self.account_id,
                    Position.mode == "LIVE",
                )
                pos_res = await session.execute(pos_stmt)
                db_position = pos_res.scalar_one_or_none()
                db_signed_size = 0.0
                if db_position is not None:
                    raw_qty = float(db_position.quantity)
                    db_signed_size = raw_qty if str(db_position.direction).upper() == "BUY" else -raw_qty
                db_upnl = float(db_position.unrealized_pnl) if db_position else 0.0

                # 3c. Local cash in DB
                acc_stmt = select(LiveAccount).where(LiveAccount.account_id == self.account_id)
                acc_res = await session.execute(acc_stmt)
                db_acc = acc_res.scalar_one_or_none()
                db_cash = float(db_acc.current_cash) if db_acc else 0.0

                # Step 4: Reconcile Orders
                ex_orders_by_cid = {
                    o.get("client_order_id"): o for o in ex_orders_raw if o.get("client_order_id")
                }
                ex_orders_by_id = {
                    str(o.get("id")): o for o in ex_orders_raw if o.get("id") is not None
                }

                in_flight_buy_volume = 0.0
                in_flight_sell_volume = 0.0

                for o in db_active_orders:
                    cid = o.client_order_id
                    age_seconds = (now_utc - (o.created_at.replace(tzinfo=timezone.utc) if o.created_at.tzinfo is None else o.created_at)).total_seconds()
                    
                    matched_ex_order = ex_orders_by_cid.get(cid) or (
                        ex_orders_by_id.get(str(o.exchange_order_id)) if o.exchange_order_id else None
                    )

                    if matched_ex_order is not None:
                        # Order is open on exchange: check fill quantity
                        ex_unfilled = float(matched_ex_order.get("unfilled_size", matched_ex_order.get("size", 0)))
                        ex_size = float(matched_ex_order.get("size", 0))
                        ex_filled = ex_size - ex_unfilled

                        if ex_filled > o.filled_quantity and ex_filled > 0:
                            # Partial fill detected on exchange not yet processed
                            logger.info(f"Reconciling partial fill for '{cid}': ex_filled={ex_filled}, db_filled={o.filled_quantity}")
                            o.filled_quantity = ex_filled
                            o.state = OrderState.PARTIALLY_FILLED.value
                            ev = LiveOrderEvent(
                                id=str(uuid.uuid4()),
                                order_id=o.order_id,
                                from_state=o.state,
                                to_state=OrderState.PARTIALLY_FILLED.value,
                                event_type="RECONCILIATION_PARTIAL_FILL",
                                payload=json.dumps({"filled_quantity": ex_filled, "source": "reconciliation_daemon"}),
                                timestamp=now_utc,
                            )
                            session.add(ev)
                            if self.order_router is not None:
                                await self.order_router.handle_partial_fill(cid, ex_filled)

                        # Accumulate in-flight directional volume for un-filled portion
                        remaining = ex_size - ex_filled
                        if str(o.side).upper() == "BUY":
                            in_flight_buy_volume += remaining
                        else:
                            in_flight_sell_volume += remaining
                    else:
                        # Order is active in DB but NOT open on exchange matching engine
                        if age_seconds <= self.in_flight_grace_period_seconds:
                            logger.info(
                                f"Order '{cid}' within in-flight grace period ({age_seconds:.1f}s <= {self.in_flight_grace_period_seconds}s); holding"
                            )
                            if str(o.side).upper() == "BUY":
                                in_flight_buy_volume += (o.quantity - o.filled_quantity)
                            else:
                                in_flight_sell_volume += (o.quantity - o.filled_quantity)
                        else:
                            # Beyond grace period: query exchange REST for terminal state
                            try:
                                ex_single = await self.client.get_order_by_client_id(client_order_id=cid)
                                terminal_state = str(ex_single.get("state", "cancelled")).lower()
                                logger.info(f"Synchronizing terminal order '{cid}' to state '{terminal_state}'")
                                if terminal_state == "filled":
                                    o.state = OrderState.FILLED.value
                                    o.filled_quantity = o.quantity
                                    ev = LiveOrderEvent(
                                        id=str(uuid.uuid4()),
                                        order_id=o.order_id,
                                        from_state=o.state,
                                        to_state=OrderState.FILLED.value,
                                        event_type="RECONCILIATION_FILL_DETECTED",
                                        payload=json.dumps({"source": "reconciliation_sync"}),
                                        timestamp=now_utc,
                                    )
                                    session.add(ev)
                                else:
                                    o.state = OrderState.CANCELLED.value
                                    ev = LiveOrderEvent(
                                        id=str(uuid.uuid4()),
                                        order_id=o.order_id,
                                        from_state=o.state,
                                        to_state=OrderState.CANCELLED.value,
                                        event_type="RECONCILIATION_CANCEL_DETECTED",
                                        payload=json.dumps({"source": "reconciliation_sync", "terminal_state": terminal_state}),
                                        timestamp=now_utc,
                                    )
                                    session.add(ev)
                            except Exception as sync_exc:
                                # Order genuinely missing or lookup failed: escalate to RECONCILING
                                logger.warning(f"Order '{cid}' missing on exchange after {age_seconds:.1f}s; escalating to RECONCILING")
                                o.state = OrderState.RECONCILING.value
                                ev = LiveOrderEvent(
                                    id=str(uuid.uuid4()),
                                    order_id=o.order_id,
                                    from_state=o.state,
                                    to_state=OrderState.RECONCILING.value,
                                    event_type="MISSING_EXCHANGE_ORDER_ESCALATION",
                                    payload=json.dumps({"error": str(sync_exc), "age_seconds": age_seconds}),
                                    timestamp=now_utc,
                                )
                                session.add(ev)
                                discrepancies.append(f"MISSING_ORDER_{cid}")

                # Check for rogue exchange orders (unknown to DB)
                db_cids = {o.client_order_id for o in db_active_orders}
                for ex_o in ex_orders_raw:
                    ex_cid = ex_o.get("client_order_id")
                    if ex_cid and ex_cid.startswith("AGY_") and ex_cid not in db_cids:
                        logger.critical(f"UNKNOWN ORDER ON EXCHANGE: {ex_cid}")
                        discrepancies.append(f"UNKNOWN_ORDER_{ex_cid}")

                # Step 5a: Reconcile Position Quantity (signed directional comparison)
                delta_q = ex_signed_size - db_signed_size
                abs_delta = abs(delta_q)
                if abs_delta > 1e-6:
                    correlated = False
                    if delta_q > 0 and delta_q <= in_flight_buy_volume and in_flight_buy_volume > 0:
                        correlated = True
                    elif delta_q < 0 and abs_delta <= in_flight_sell_volume and in_flight_sell_volume > 0:
                        correlated = True

                    if correlated:
                        logger.info(
                            f"Position difference delta_q={delta_q} correlated with in-flight orders "
                            f"(buy_inflight={in_flight_buy_volume}, sell_inflight={in_flight_sell_volume}); holding alarm"
                        )
                    else:
                        # Unexplained position mismatch: Ghost Position detected!
                        logger.critical(
                            f"GHOST POSITION DETECTED: exchange={ex_signed_size}, db={db_signed_size}, delta={delta_q}"
                        )
                        discrepancies.append(f"GHOST_POSITION_DELTA_{abs_delta}")
                        self.ghost_positions_detected.append({
                            "ex_position": ex_signed_size,
                            "db_position": db_signed_size,
                            "delta_q": delta_q,
                            "detected_at": now_utc.isoformat(),
                        })

                        # Block new trading and latch Kill Switch without automated liquidation
                        if self.order_router is not None:
                            self.order_router.kill_switch_state = "MANUAL_RESET_REQUIRED"

                # Step 5b: Reconcile Cash Balance with Funding-Aware Tolerance
                tick_value = 0.01
                base_cash_tol = max(10.0 * tick_value, 0.0001 * max(abs(db_cash), 1.0))
                in_funding_window = is_in_funding_settlement_window(now_utc)
                tol_funding = self.funding_tolerance_usd if in_funding_window else 0.0
                total_cash_tol = base_cash_tol + tol_funding
                cash_diff = abs(ex_cash - db_cash)
                if cash_diff > total_cash_tol and (ex_cash > 0 or db_cash > 0):
                    logger.warning(
                        f"Cash balance tolerance breach: db_cash={db_cash}, ex_cash={ex_cash}, "
                        f"diff={cash_diff:.2f} > tol={total_cash_tol:.2f} (funding_window={in_funding_window})"
                    )
                    discrepancies.append(f"CASH_TOLERANCE_BREACH_{cash_diff:.2f}_VS_{total_cash_tol:.2f}")

                # Step 5c: Reconcile Unrealized PnL with Basis-Aware Tolerance
                abs_pos_size = abs(ex_signed_size)
                if abs_pos_size > 1e-6 and abs_delta <= 1e-6:
                    tol_upnl = abs_pos_size * self.contract_value * max(self.current_spread_usd, 3.0 * self.basis_twap_usd)
                    upnl_diff = abs(ex_upnl - db_upnl)
                    if upnl_diff > tol_upnl:
                        logger.warning(
                            f"UPnL tolerance breach: db_upnl={db_upnl}, ex_upnl={ex_upnl}, "
                            f"diff={upnl_diff:.2f} > tol={tol_upnl:.2f}"
                        )
                        discrepancies.append(f"UPNL_TOLERANCE_BREACH_{upnl_diff:.2f}_VS_{tol_upnl:.2f}")

                # Step 6a: Reconcile Bracket Stop Loss (both Long and Short positions)
                abs_ex_pos = abs(ex_signed_size)
                if abs_ex_pos > 1e-6:
                    resting_sl_size = 0.0
                    for ex_o in ex_orders_raw:
                        if ex_o.get("order_type") in ("stop_market", "stop_limit"):
                            if ex_o.get("reduce_only", False):
                                resting_sl_size += float(ex_o.get("size", 0.0))

                    if resting_sl_size > 0 and abs(resting_sl_size - abs_ex_pos) > 1e-6:
                        logger.warning(
                            f"Bracket mismatch: resting SL size={resting_sl_size} != position size={abs_ex_pos}"
                        )
                        discrepancies.append(f"BRACKET_SIZE_MISMATCH_{resting_sl_size}_VS_{abs_ex_pos}")

                # Step 6b: Sweep Orphaned Synthetic OCO Companion Orders when Position is Zero
                if abs_ex_pos < 1e-6 and abs(db_signed_size) < 1e-6:
                    await self._sweep_orphaned_oco_orders_in_cycle(ex_orders_raw, session, now_utc)

                # Step 7: Update and Persist Snapshot
                total_discrepancies = len(discrepancies)
                self.discrepancy_count = total_discrepancies

                if self.order_router is not None:
                    self.order_router.discrepancy_count = total_discrepancies

                status_str = "CLEAN"
                if any("GHOST_POSITION" in d for d in discrepancies):
                    status_str = "GHOST_POSITION_DETECTED"
                elif total_discrepancies > 0:
                    status_str = "DISCREPANCY_DETECTED"

                snapshot_id = str(uuid.uuid4())
                snapshot = ReconciliationSnapshot(
                    snapshot_id=snapshot_id,
                    account_id=self.account_id,
                    timestamp=now_utc,
                    discrepancy_count=total_discrepancies,
                    cash_local=db_cash,
                    cash_exchange=ex_cash,
                    upnl_local=db_upnl,
                    upnl_exchange=ex_upnl,
                    status=status_str,
                    details=json.dumps({
                        "discrepancies": discrepancies,
                        "ex_position": ex_signed_size,
                        "db_position": db_signed_size,
                        "active_db_orders": len(db_active_orders),
                        "ex_orders": len(ex_orders_raw),
                        "funding_window": in_funding_window,
                    }),
                )
                session.add(snapshot)
                await session.commit()
                self.last_snapshot_id = snapshot_id

            return {
                "status": status_str,
                "snapshot_id": snapshot_id,
                "discrepancy_count": total_discrepancies,
                "discrepancies": discrepancies,
                "ex_position": ex_signed_size,
                "db_position": db_signed_size,
                "timestamp": now_utc.isoformat(),
            }

    async def _sweep_orphaned_oco_orders_in_cycle(
        self,
        ex_orders_raw: List[Dict[str, Any]],
        session: AsyncSession,
        now_utc: datetime,
    ) -> List[str]:
        """Internal helper to sweep orphaned reduce_only companion orders during reconciliation."""
        swept: List[str] = []
        # Cancel open reduce_only orders on exchange
        for o in ex_orders_raw:
            is_reduce_only = bool(o.get("reduce_only", False))
            is_stop = str(o.get("order_type", "")).lower() in ("stop_market", "stop_limit")
            if is_reduce_only or is_stop:
                o_id = o.get("id")
                client_id = o.get("client_order_id")
                try:
                    await self.client.cancel_order(product_id=self.product_id, order_id=o_id)
                    swept.append(client_id or str(o_id))
                    logger.info(f"Swept orphaned reduce_only exchange order id={o_id}, client_id={client_id}")
                except Exception as exc:
                    logger.warning(f"Failed to cancel orphaned order id={o_id}: {exc}")

        # Cancel matching DB active orders
        db_stmt = select(LiveOrder).where(
            LiveOrder.account_id == self.account_id,
            LiveOrder.state.in_([OrderState.ACCEPTED.value, OrderState.SUBMITTED.value, OrderState.PARTIALLY_FILLED.value]),
            LiveOrder.reduce_only == True,
        )
        res = await session.execute(db_stmt)
        db_orphans = res.scalars().all()
        for dbo in db_orphans:
            dbo.state = OrderState.CANCELLED.value
            ev = LiveOrderEvent(
                id=str(uuid.uuid4()),
                order_id=dbo.order_id,
                from_state=dbo.state,
                to_state=OrderState.CANCELLED.value,
                event_type=OrderEvent.CANCEL.value,
                payload=json.dumps({"reason": "ORPHANED_OCO_SWEEP", "timestamp": now_utc.isoformat()}),
                timestamp=now_utc,
            )
            session.add(ev)
            if dbo.client_order_id not in swept:
                swept.append(dbo.client_order_id)

        if self.order_router is not None:
            for cid in swept:
                self.order_router._active_exit_orders.discard(cid)

        return swept

    async def sweep_orphaned_oco_orders(self) -> List[str]:
        """Public method to sweep orphaned reduce_only companion orders when position is flat."""
        async with self._lock:
            now_utc = datetime.now(timezone.utc)
            try:
                ex_positions_raw = await self.client.get_positions()
                ex_signed_size = 0.0
                for p in ex_positions_raw:
                    p_id = p.get("product_id")
                    if p_id == self.product_id or str(p_id) == str(self.product_id):
                        raw_sz = float(p.get("size", 0.0))
                        side = str(p.get("side", "")).lower()
                        ex_signed_size = -raw_sz if side == "sell" and raw_sz > 0 else raw_sz
                        break

                if abs(ex_signed_size) < 1e-6:
                    ex_orders_raw = await self.client.get_orders(state="open")
                    async with self.session_factory() as session:
                        swept = await self._sweep_orphaned_oco_orders_in_cycle(ex_orders_raw, session, now_utc)
                        await session.commit()
                        return swept
            except Exception as exc:
                logger.error(f"Error during sweep_orphaned_oco_orders: {exc}")
        return []

    async def record_funding_event(
        self,
        funding_rate: float,
        payment_amount: float,
        product_symbol: Optional[str] = None,
    ) -> LiveFundingEvent:
        """Record an 8-hour perpetual funding payment into live_funding_events and update local cash."""
        now_utc = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            event = LiveFundingEvent(
                event_id=str(uuid.uuid4()),
                account_id=self.account_id,
                product_symbol=product_symbol or self.product_symbol,
                funding_rate=funding_rate,
                payment_amount=payment_amount,
                timestamp=now_utc,
            )
            session.add(event)

            # Update local account cash balance with the funding settlement
            acc_stmt = select(LiveAccount).where(LiveAccount.account_id == self.account_id)
            acc_res = await session.execute(acc_stmt)
            acc = acc_res.scalar_one_or_none()
            if acc is not None:
                acc.current_cash = float(acc.current_cash) + payment_amount

            await session.commit()
            logger.info(
                f"Recorded LiveFundingEvent: account={self.account_id}, "
                f"rate={funding_rate}, payment={payment_amount}"
            )
            return event

    async def get_status(self) -> Dict[str, Any]:
        """Query real-time operational status and discrepancy counts."""
        is_leader = True
        if self.lease_manager is not None:
            is_leader = await self.lease_manager.is_leader_valid()

        status_str = "CLEAN"
        if len(self.ghost_positions_detected) > 0:
            status_str = "GHOST_POSITION_DETECTED"
        elif self.discrepancy_count > 0:
            status_str = "DISCREPANCY_DETECTED"
        elif not is_leader:
            status_str = "STANDBY_NOT_LEADER"

        return {
            "status": status_str,
            "account_id": self.account_id,
            "product_symbol": self.product_symbol,
            "discrepancy_count": self.discrepancy_count,
            "ghost_positions": list(self.ghost_positions_detected),
            "is_leader": is_leader,
            "last_snapshot_id": self.last_snapshot_id,
            "consecutive_transport_failures": self.consecutive_transport_failures,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def get_snapshots(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Query historical reconciliation audit snapshots."""
        async with self.session_factory() as session:
            stmt = (
                select(ReconciliationSnapshot)
                .where(ReconciliationSnapshot.account_id == self.account_id)
                .order_by(ReconciliationSnapshot.timestamp.desc())
                .limit(limit)
                .offset(offset)
            )
            res = await session.execute(stmt)
            rows = res.scalars().all()
            return [
                {
                    "snapshot_id": s.snapshot_id,
                    "account_id": s.account_id,
                    "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                    "discrepancy_count": s.discrepancy_count,
                    "cash_local": s.cash_local,
                    "cash_exchange": s.cash_exchange,
                    "upnl_local": s.upnl_local,
                    "upnl_exchange": s.upnl_exchange,
                    "status": s.status,
                    "details": json.loads(s.details) if s.details else {},
                }
                for s in rows
            ]

    async def adopt_ghost_position(
        self,
        operator_id: str,
        product_id: int,
        adopted_quantity: float,
        realized_price: float,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Operator Remediation: adopt an unexplained exchange position into the database.
        
        Updates local database position record to match Delta Exchange reality,
        logs an immutable audit record in operator_reconciliation_audit_logs,
        and re-runs reconciliation.
        """
        async with self._lock:
            now_utc = datetime.now(timezone.utc)
            ex_upnl = 0.0
            if self.client is not None:
                try:
                    positions_raw = await self.client.get_positions()
                    for p in positions_raw:
                        p_id = p.get("product_id")
                        if p_id == product_id or str(p_id) == str(product_id) or (self.product_id and (p_id == self.product_id or str(p_id) == str(self.product_id))):
                            ex_upnl = float(p.get("unrealized_pnl", 0.0))
                            break
                except Exception as exc:
                    logger.warning(f"Could not fetch exchange position UPnL during ghost adoption: {exc}")

            async with self.session_factory() as session:
                pos_stmt = select(Position).where(
                    Position.live_account_id == self.account_id,
                    Position.mode == "LIVE",
                )
                pos_res = await session.execute(pos_stmt)
                db_pos = pos_res.scalar_one_or_none()

                if db_pos is None:
                    # Ensure instrument exists to satisfy foreign key constraint
                    inst_stmt = select(Instrument).where(Instrument.id == self.product_symbol)
                    inst_res = await session.execute(inst_stmt)
                    if inst_res.scalar_one_or_none() is None:
                        inst = Instrument(
                            id=self.product_symbol,
                            symbol=self.product_symbol,
                            name=f"{self.product_symbol} Perpetual",
                            asset_class="COMMODITY",
                            quote_currency="USDT",
                        )
                        session.add(inst)
                        await session.flush()

                    db_pos = Position(
                        instrument_id=self.product_symbol,
                        live_account_id=self.account_id,
                        mode="LIVE",
                        direction="BUY" if adopted_quantity > 0 else "SELL",
                        quantity=abs(adopted_quantity),
                        average_entry_price=realized_price,
                        current_mark_price=realized_price,
                        unrealized_pnl=ex_upnl,
                    )
                    session.add(db_pos)
                else:
                    db_pos.quantity = abs(adopted_quantity)
                    db_pos.direction = "BUY" if adopted_quantity > 0 else "SELL"
                    db_pos.average_entry_price = realized_price
                    db_pos.current_mark_price = realized_price
                    db_pos.unrealized_pnl = ex_upnl

                audit_log = OperatorReconciliationAuditLog(
                    log_id=str(uuid.uuid4()),
                    snapshot_id=self.last_snapshot_id,
                    action_type="ADOPT_GHOST_POSITION",
                    operator_id=operator_id,
                    parameters=json.dumps({
                        "product_id": product_id,
                        "adopted_quantity": adopted_quantity,
                        "realized_price": realized_price,
                        "notes": notes,
                    }),
                    timestamp=now_utc,
                )
                session.add(audit_log)
                await session.commit()

            # Clear ghost positions cache
            self.ghost_positions_detected.clear()

        # Re-run reconciliation cycle to update snapshot
        return await self.run_reconciliation_cycle()

    async def start(self) -> None:
        """Start background reconciliation daemon loop."""
        if self._is_running:
            return
        self._is_running = True

        async def _loop():
            while self._is_running:
                try:
                    await self.run_reconciliation_cycle()
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.error(f"Unexpected error in reconciliation daemon loop: {exc}")
                await asyncio.sleep(self.reconciliation_interval_seconds)

        self._daemon_task = asyncio.create_task(_loop())
        logger.info(f"Reconciliation daemon started with interval={self.reconciliation_interval_seconds}s")

    async def stop(self) -> None:
        """Stop background reconciliation daemon loop."""
        self._is_running = False
        if self._daemon_task is not None:
            self._daemon_task.cancel()
            try:
                await self._daemon_task
            except asyncio.CancelledError:
                pass
            self._daemon_task = None
        logger.info("Reconciliation daemon stopped")


class ReconciliationService:
    """Global service registry coordinating ReconciliationDaemon and LiveOrderRouter."""

    _instance: Optional["ReconciliationService"] = None

    def __init__(self):
        self._daemon: Optional[ReconciliationDaemon] = None
        self._router: Optional[LiveOrderRouter] = None
        self._live_kill_switch: Optional[Any] = None
        self._live_risk_engine: Optional[Any] = None

    @classmethod
    def get_instance(cls) -> "ReconciliationService":
        if cls._instance is None:
            cls._instance = ReconciliationService()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton for clean testing isolation."""
        cls._instance = None

    def register_daemon(self, daemon: ReconciliationDaemon) -> None:
        self._daemon = daemon

    def get_daemon(self) -> Optional[ReconciliationDaemon]:
        return self._daemon

    def register_router(self, router: LiveOrderRouter) -> None:
        self._router = router

    def get_router(self) -> Optional[LiveOrderRouter]:
        return self._router

    def register_live_kill_switch(self, kill_switch: Any) -> None:
        self._live_kill_switch = kill_switch

    def get_live_kill_switch(self) -> Optional[Any]:
        return self._live_kill_switch

    def register_live_risk_engine(self, engine: Any) -> None:
        self._live_risk_engine = engine

    def get_live_risk_engine(self) -> Optional[Any]:
        return self._live_risk_engine

    def get_discrepancy_count(self) -> int:
        if self._daemon is not None:
            return self._daemon.discrepancy_count
        if self._router is not None:
            return self._router.discrepancy_count
        return 0

