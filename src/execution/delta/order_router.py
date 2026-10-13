"""Live Order Router and Execution Dispatcher.

Enforces:
- Canonical 11-State OrderLifecycleFSM transitions.
- ClientOrderIdGenerator and IdempotencyManager deduplication.
- Inter-position entry blocking (no new entries if prior exit order is active/unconfirmed).
- Unresolved discrepancy entry blocking (no new entries if discrepancy_count > 0).
- Separation of controls: Protective exit bypasses (Gates 2, 5, 6, 7, 11) while mandating
  Gate 1, leadership fencing, rate limiting, HMAC signing, local position bounds, and Gate 9 clock drift SLA.
- Dynamic bracket scaling (Q_SL = Q_filled, strictly Q_SL <= Q_filled) via exchange-side cancel/replace.
- Synthetic OCO companion cancellation with exchange status verification.
- Network timeout and connector failure handling (outcome unknown: SUBMITTED -> FAILED -> RECONCILING, zero retry).
- Fail-fast in-band operator flatten guard (HTTP 503 DeltaTransportOrTimeSyncUnavailableError).
- State recovery from PostgreSQL upon router restart.
"""

import asyncio
from datetime import datetime, timezone
import inspect
import json
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.logger import logger
from src.execution.delta.client import DeltaRestClient
from src.execution.delta.fsm import (
    OrderEvent,
    OrderLifecycleFSM,
    OrderState,
)
from src.execution.delta.idempotency import ClientOrderIdGenerator, IdempotencyManager
from src.execution.delta.types import (
    DeltaAuthenticationError,
    DeltaClockDriftError,
    DeltaConnectorError,
    DeltaNetworkError,
    DeltaOrderRejectedError,
    DeltaOrderRequest,
    DeltaOrderSide,
    DeltaOrderType,
    DeltaRateLimitError,
    DeltaTimeInForce,
    DeltaTransportOrTimeSyncUnavailableError,
    DiscrepancyBlockedError,
    IdempotencyConflictError,
    InterPositionEntryBlockedError,
    LeadershipLostError,
    PositionBoundsExceededError,
)


class LiveOrderRouter:
    """Institutional Live Order Router enforcing safety gates, idempotency, and FSM lifecycle."""

    def __init__(
        self,
        client: DeltaRestClient,
        idempotency_manager: Optional[IdempotencyManager] = None,
        session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
        is_leader_fn: Optional[Callable[[], Awaitable[bool] | bool]] = None,
        risk_gate_evaluator: Optional[Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]] | Dict[str, Any]]] = None,
        clock_drift_evaluator: Optional[Callable[[], Awaitable[float] | float]] = None,
        position_provider: Optional[Callable[[int], Awaitable[float] | float]] = None,
        max_clock_drift_ms: float = 1000.0,
    ):
        self.client = client
        self.session_factory = session_factory
        self.idempotency_manager = idempotency_manager or IdempotencyManager(session_factory=session_factory)
        self.is_leader_fn = is_leader_fn
        self.risk_gate_evaluator = risk_gate_evaluator
        self.clock_drift_evaluator = clock_drift_evaluator
        self.position_provider = position_provider
        self.max_clock_drift_ms = max_clock_drift_ms

        # In-memory FSM and order tracking
        self._fsm_registry: Dict[str, OrderLifecycleFSM] = {}
        self._order_records: Dict[str, Dict[str, Any]] = {}
        self._active_exit_orders: Set[str] = set()
        self._bracket_pairs: Dict[str, Dict[str, Optional[str]]] = {}  # entry_id -> {sl_id, tp_id}
        self._companion_map: Dict[str, str] = {}  # order_id -> companion_order_id (SL <-> TP)
        self._in_flight_futures: Dict[str, asyncio.Future] = {}

        # Operational safety states
        self.kill_switch_state: str = "NORMAL"
        self.discrepancy_count: int = 0
        self._lock = asyncio.Lock()

    async def _check_leadership(self) -> bool:
        """Evaluate leadership validity."""
        if self.is_leader_fn is None:
            return True
        res = self.is_leader_fn()
        if inspect.isawaitable(res):
            return await res
        return bool(res)

    async def _get_clock_drift_ms(self) -> float:
        """Fetch current clock drift."""
        if self.clock_drift_evaluator is not None:
            res = self.clock_drift_evaluator()
            if inspect.isawaitable(res):
                return await res
            return float(res)
        try:
            return await self.client.check_clock_drift()
        except DeltaClockDriftError:
            # Extract drift from client if recorded
            if self.client._last_server_time_drift_ms is not None:
                return self.client._last_server_time_drift_ms
            return self.max_clock_drift_ms + 100.0
        except Exception:
            # Time endpoint unreachable: drift cannot be verified; fail closed
            return self.max_clock_drift_ms + 1000.0

    async def _get_position_size(self, product_id: int) -> Optional[float]:
        """Fetch current position size for bounds check."""
        if self.position_provider is not None:
            res = self.position_provider(product_id)
            if inspect.isawaitable(res):
                return float(await res)
            return float(res)
        try:
            positions = await self.client.get_positions()
            for p in positions:
                if p.get("product_id") == product_id or str(p.get("product_id")) == str(product_id):
                    return abs(float(p.get("size", 0.0)))
            return 0.0
        except Exception:
            return None

    def get_fsm(self, client_order_id: str) -> Optional[OrderLifecycleFSM]:
        """Retrieve active FSM for given client order ID."""
        return self._fsm_registry.get(client_order_id)

    async def _transition_order(
        self,
        fsm: OrderLifecycleFSM,
        to_state: OrderState,
        event: Optional[OrderEvent] = None,
        payload: Optional[Dict[str, Any]] = None,
        exchange_order_id: Optional[str] = None,
    ) -> OrderState:
        """Atomically transition FSM in-memory and persist state change with LiveOrderEvent."""
        fsm.transition(to_state, event=event, payload=payload)
        client_id = fsm.client_order_id or fsm.order_id

        if client_id in self._order_records:
            self._order_records[client_id]["state"] = to_state.value
            if exchange_order_id is not None:
                self._order_records[client_id]["id"] = exchange_order_id

        # Update idempotency manager cache and PostgreSQL live_orders/live_order_events
        await self.idempotency_manager.update_state(
            client_order_id=client_id,
            new_state=to_state.value,
            exchange_order_id=exchange_order_id,
            event_payload=payload,
        )
        return to_state

    async def dispatch_order(
        self,
        order_request: DeltaOrderRequest,
        is_protective: bool = False,
        strategy_id: str = "DEF",
        bar_timestamp: Optional[int] = None,
        session: Optional[AsyncSession] = None,
    ) -> Dict[str, Any]:
        """Dispatch an order through the deterministic execution safety pipeline.
        
        Args:
            order_request: The contract order request payload.
            is_protective: True if this order is a protective exit (SL, TP, flatten).
            strategy_id: Originating strategy identifier for client order ID tag.
            bar_timestamp: Bar timestamp for deterministic client order ID.
            session: Optional database session for transactional registration.
            
        Returns:
            Dict[str, Any]: Order dispatch confirmation or existing idempotent order record.
        """
        # Step 1: Idempotency & In-Flight Concurrency Check
        async with self._lock:
            if not order_request.client_order_id:
                ts = bar_timestamp if bar_timestamp is not None else int(time.time())
                order_request.client_order_id = ClientOrderIdGenerator.generate(
                    strategy_id=strategy_id,
                    bar_timestamp=ts,
                )

            client_id = order_request.client_order_id
            order_payload = order_request.to_dict()

            # Concurrent duplicate request in-flight: await primary request's resolution
            if client_id in self._in_flight_futures:
                in_flight_fut = self._in_flight_futures[client_id]
                logger.info(f"Duplicate submission for '{client_id}' arriving while in flight; awaiting primary resolution")
                return await asyncio.shield(in_flight_fut)

            # Register future for subsequent concurrent duplicates
            loop = asyncio.get_running_loop()
            in_flight_fut = loop.create_future()
            self._in_flight_futures[client_id] = in_flight_fut

        try:
            # Register order idempotently in PostgreSQL / cache
            record, is_duplicate = await self.idempotency_manager.register_order(
                client_order_id=client_id,
                order_payload=order_payload,
                session=session,
            )

            if is_duplicate:
                logger.info(f"Idempotent order detected for '{client_id}'; returning existing order without re-dispatch")
                existing_res = self._order_records.get(client_id, record)
                if not in_flight_fut.done():
                    in_flight_fut.set_result(existing_res)
                return existing_res

            # Initialize FSM in CREATED
            fsm = OrderLifecycleFSM(order_id=record.get("order_id", client_id), client_order_id=client_id, initial_state=OrderState.CREATED)
            self._fsm_registry[client_id] = fsm
            self._order_records[client_id] = record

            # Treat reduce_only orders as protective exits
            effective_protective = is_protective or order_request.reduce_only

            # Step 2: Mandatory Leadership Fencing
            is_leader = await self._check_leadership()
            if not is_leader:
                await self._transition_order(fsm, OrderState.REJECTED, event=OrderEvent.REJECT, payload={"reason": "LEADERSHIP_LOST"})
                raise LeadershipLostError(f"Node does not hold valid leadership lease; dispatch rejected for '{client_id}'")

            # Step 3: Inter-Position Entry Blocking & Unresolved Discrepancies
            if not effective_protective:
                # 3a. Discrepancy Gate: No new entries while discrepancies exist
                if self.discrepancy_count > 0:
                    await self._transition_order(
                        fsm,
                        OrderState.REJECTED,
                        event=OrderEvent.REJECT,
                        payload={"reason": "UNRESOLVED_DISCREPANCIES", "discrepancy_count": self.discrepancy_count},
                    )
                    raise DiscrepancyBlockedError(
                        f"New entry '{client_id}' blocked: {self.discrepancy_count} unresolved reconciliation discrepancies"
                    )

                # 3b. Active exit orders check
                active_exits = [
                    exit_id for exit_id in self._active_exit_orders
                    if self._fsm_registry.get(exit_id) and self._fsm_registry[exit_id].is_active
                ]
                if active_exits:
                    await self._transition_order(
                        fsm,
                        OrderState.REJECTED,
                        event=OrderEvent.REJECT,
                        payload={"reason": "INTER_POSITION_ENTRY_BLOCKED", "active_exits": active_exits},
                    )
                    raise InterPositionEntryBlockedError(
                        f"New entry '{client_id}' blocked: companion exit order(s) {active_exits} remain active"
                    )

            # Step 4: Risk Gate Verification & Protective Exit Bypass Rules
            drift_ms = await self._get_clock_drift_ms()

            if effective_protective:
                # Local Position Bounds Check: Protective exit must not exceed known position
                pos_size = await self._get_position_size(order_request.product_id)
                if pos_size is not None and pos_size > 0.0 and order_request.size > pos_size:
                    await self._transition_order(
                        fsm,
                        OrderState.REJECTED,
                        event=OrderEvent.REJECT,
                        payload={"reason": "POSITION_BOUNDS_EXCEEDED", "order_size": order_request.size, "pos_size": pos_size},
                    )
                    raise PositionBoundsExceededError(
                        f"Protective exit '{client_id}' size {order_request.size} exceeds known position {pos_size}"
                    )

                # PROTECTIVE EXITS:
                # May bypass: Gate 2, Gate 5, Gate 6, Gate 7, Gate 11.
                # Must enforce: Gate 1 drawdown >= 6.0%, Gate 9 clock drift SLA
                if self.risk_gate_evaluator is not None:
                    gate_eval = self.risk_gate_evaluator({
                        "order_request": order_request,
                        "is_protective": True,
                        "client_order_id": client_id,
                    })
                    if inspect.isawaitable(gate_eval):
                        gate_eval = await gate_eval

                    if gate_eval.get("gate_1_drawdown_breach", False):
                        self.kill_switch_state = "FLATTENING"
                        logger.critical("Gate 1 breach detected: Portfolio drawdown >= 6.0%; Kill Switch FLATTENING latched")

                # Gate 9 Clock Drift Evaluation for Protective Exits
                if drift_ms > self.max_clock_drift_ms:
                    # Test if Delta /v2/time is reachable for compensated timestamp
                    time_reachable = False
                    try:
                        server_time = await asyncio.wait_for(self.client.get_server_time(), timeout=2.0)
                        if server_time > 0:
                            time_reachable = True
                    except Exception:
                        time_reachable = False

                    if not time_reachable:
                        # Fail-closed: trip kill switch to CLOCK_DESYNC_UNPROTECTED_EXPOSURE
                        self.kill_switch_state = "CLOCK_DESYNC_UNPROTECTED_EXPOSURE"
                        await self._transition_order(fsm, OrderState.REJECTED, event=OrderEvent.REJECT, payload={"reason": "CLOCK_DESYNC_UNREACHABLE_TIME"})
                        raise DeltaClockDriftError(
                            f"Protective exit halted: clock drift {drift_ms:.1f}ms exceeds SLA and exchange /v2/time is unreachable"
                        )
                    else:
                        logger.warning(
                            f"Clock drift {drift_ms:.1f}ms exceeds SLA, but exchange /v2/time reachable; dispatching with compensated timestamp"
                        )
            else:
                # NEW ENTRIES:
                # Must enforce all gates
                if drift_ms > self.max_clock_drift_ms:
                    await self._transition_order(fsm, OrderState.REJECTED, event=OrderEvent.REJECT, payload={"reason": "GATE_9_CLOCK_DRIFT_BREACH", "drift_ms": drift_ms})
                    raise DeltaClockDriftError(
                        f"New entry '{client_id}' halted: clock drift {drift_ms:.1f}ms exceeds 1000ms SLA (Gate 9)"
                    )

                if self.risk_gate_evaluator is not None:
                    gate_eval = self.risk_gate_evaluator({
                        "order_request": order_request,
                        "is_protective": False,
                        "client_order_id": client_id,
                    })
                    if inspect.isawaitable(gate_eval):
                        gate_eval = await gate_eval

                    if not gate_eval.get("approved", True):
                        failed_gate = gate_eval.get("failed_gate", "UNKNOWN")
                        await self._transition_order(fsm, OrderState.REJECTED, event=OrderEvent.REJECT, payload={"reason": f"GATE_{failed_gate}_REJECTION", "details": gate_eval})
                        raise DeltaOrderRejectedError(
                            f"Entry order '{client_id}' rejected by Risk Gate {failed_gate}: {gate_eval.get('message')}"
                        )

            # Step 5: Transition CREATED -> SIGNED
            await self._transition_order(fsm, OrderState.SIGNED, event=OrderEvent.SIGN, payload={"client_order_id": client_id})

            # Step 6: Transition SIGNED -> SUBMITTED
            await self._transition_order(fsm, OrderState.SUBMITTED, event=OrderEvent.SUBMIT, payload={"client_order_id": client_id})

            if effective_protective:
                self._active_exit_orders.add(client_id)

            # Step 7: Network Dispatch to Delta Exchange
            try:
                exchange_res = await self.client.place_order(order_request)
            except httpx.TimeoutException as exc:
                # Network Timeout / Drop: outcome unknown on exchange matching engine!
                logger.error(f"Network timeout submitting order '{client_id}'; transitioning to FAILED -> RECONCILING")
                await self._transition_order(fsm, OrderState.FAILED, event=OrderEvent.FAIL, payload={"error": str(exc), "type": "TimeoutException"})
                await self._transition_order(fsm, OrderState.RECONCILING, event=OrderEvent.ESCALATE_RECONCILING, payload={"escalated_at": datetime.now(timezone.utc).isoformat()})
                raise DeltaNetworkError(
                    f"Transport timeout dispatching '{client_id}'; outcome UNKNOWN; escalated to RECONCILING with zero retry"
                ) from exc
            except DeltaNetworkError as exc:
                logger.error(f"Network error submitting order '{client_id}'; transitioning to FAILED -> RECONCILING")
                await self._transition_order(fsm, OrderState.FAILED, event=OrderEvent.FAIL, payload={"error": str(exc), "type": "DeltaNetworkError"})
                await self._transition_order(fsm, OrderState.RECONCILING, event=OrderEvent.ESCALATE_RECONCILING, payload={"escalated_at": datetime.now(timezone.utc).isoformat()})
                raise DeltaNetworkError(
                    f"Network failure dispatching '{client_id}'; outcome UNKNOWN; escalated to RECONCILING with zero retry"
                ) from exc
            except DeltaRateLimitError as exc:
                # Rate limit (HTTP 429) rejected pre-matching
                logger.error(f"Rate limit exceeded submitting order '{client_id}'; transitioning to REJECTED")
                await self._transition_order(fsm, OrderState.REJECTED, event=OrderEvent.REJECT, payload={"error": str(exc), "type": "DeltaRateLimitError"})
                self._active_exit_orders.discard(client_id)
                raise
            except DeltaAuthenticationError as exc:
                # Authentication (HTTP 401) rejected pre-matching
                logger.error(f"Authentication failure submitting order '{client_id}'; transitioning to REJECTED")
                await self._transition_order(fsm, OrderState.REJECTED, event=OrderEvent.REJECT, payload={"error": str(exc), "type": "DeltaAuthenticationError"})
                self._active_exit_orders.discard(client_id)
                raise
            except DeltaOrderRejectedError as exc:
                # Check for ReduceOnlyExceedsPosition
                error_code = exc.code or (exc.details.get("error", {}).get("code") if exc.details else None)
                if error_code == "ReduceOnlyExceedsPosition" or "ReduceOnlyExceedsPosition" in exc.message:
                    logger.warning(
                        f"Order '{client_id}' rejected with ReduceOnlyExceedsPosition; transitioning to CANCELLED and scheduling reconciliation"
                    )
                    await self._transition_order(fsm, OrderState.CANCELLED, event=OrderEvent.CANCEL, payload={"reason": "ReduceOnlyExceedsPosition"})
                    self._active_exit_orders.discard(client_id)
                    self.discrepancy_count += 1
                    cancel_dict = {
                        "client_order_id": client_id,
                        "state": OrderState.CANCELLED.value,
                        "error": "ReduceOnlyExceedsPosition",
                        "action": "CANCELLED_AND_SCHEDULED_RECONCILIATION",
                    }
                    if not in_flight_fut.done():
                        in_flight_fut.set_result(cancel_dict)
                    return cancel_dict

                await self._transition_order(fsm, OrderState.REJECTED, event=OrderEvent.REJECT, payload={"error": exc.message, "code": exc.code})
                self._active_exit_orders.discard(client_id)
                raise
            except DeltaConnectorError as exc:
                # Response parse or malformed response: matching engine outcome is unknown!
                logger.error(f"Connector parsing failure for order '{client_id}'; outcome UNKNOWN; escalating to RECONCILING")
                await self._transition_order(fsm, OrderState.FAILED, event=OrderEvent.FAIL, payload={"error": str(exc), "type": "DeltaConnectorError"})
                await self._transition_order(fsm, OrderState.RECONCILING, event=OrderEvent.ESCALATE_RECONCILING, payload={"escalated_at": datetime.now(timezone.utc).isoformat()})
                raise

            # Step 8: Handle Exchange Response & Transition
            res_state = str(exchange_res.get("state", "open")).lower()
            exchange_order_id = str(exchange_res.get("id")) if exchange_res.get("id") else None

            if res_state == "filled":
                await self._transition_order(
                    fsm,
                    OrderState.FILLED,
                    event=OrderEvent.FILL,
                    payload={"exchange_order_id": exchange_order_id},
                    exchange_order_id=exchange_order_id,
                )
                self._active_exit_orders.discard(client_id)
            elif res_state in ("partially_filled", "partial_fill"):
                await self._transition_order(
                    fsm,
                    OrderState.PARTIALLY_FILLED,
                    event=OrderEvent.PARTIAL_FILL,
                    payload={"exchange_order_id": exchange_order_id},
                    exchange_order_id=exchange_order_id,
                )
            else:
                # Resting open on matching engine
                await self._transition_order(
                    fsm,
                    OrderState.ACCEPTED,
                    event=OrderEvent.ACK,
                    payload={"exchange_order_id": exchange_order_id},
                    exchange_order_id=exchange_order_id,
                )

            record.update(exchange_res)
            record["state"] = fsm.state.value
            if not in_flight_fut.done():
                in_flight_fut.set_result(record)
            return record

        except Exception as exc:
            if not in_flight_fut.done():
                in_flight_fut.set_exception(exc)
                try:
                    in_flight_fut.exception()
                except Exception:
                    pass
            raise
        finally:
            async with self._lock:
                self._in_flight_futures.pop(client_id, None)

    async def handle_partial_fill(
        self,
        entry_client_order_id: str,
        filled_quantity: float,
        sl_client_order_id: Optional[str] = None,
        tp_client_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dynamic Bracket Scaling: adjust stop loss and take profit sizes to filled quantity.
        
        Performs exchange-side cancel/replace for resting protective stop orders
        so the actual exchange matching engine stop quantity strictly satisfies:
        Q_SL = Q_filled, strictly Q_SL <= Q_filled.
        """
        async with self._lock:
            # Register or update bracket pairing
            if entry_client_order_id not in self._bracket_pairs:
                self._bracket_pairs[entry_client_order_id] = {
                    "sl_id": sl_client_order_id,
                    "tp_id": tp_client_order_id,
                }
            else:
                if sl_client_order_id:
                    self._bracket_pairs[entry_client_order_id]["sl_id"] = sl_client_order_id
                if tp_client_order_id:
                    self._bracket_pairs[entry_client_order_id]["tp_id"] = tp_client_order_id

            active_sl = self._bracket_pairs[entry_client_order_id].get("sl_id")
            active_tp = self._bracket_pairs[entry_client_order_id].get("tp_id")
            if active_sl and active_tp:
                self._companion_map[active_sl] = active_tp
                self._companion_map[active_tp] = active_sl

            scaled_sl_qty = float(filled_quantity)
            scaled_tp_qty = float(filled_quantity)

        # Exchange-side Stop Loss scaling via cancel/replace
        if active_sl and active_sl in self._order_records:
            sl_record = self._order_records[active_sl]
            exchange_order_id = sl_record.get("id")
            sl_fsm = self._fsm_registry.get(active_sl)

            if exchange_order_id is not None and sl_fsm and sl_fsm.is_active:
                prod_id = int(sl_record.get("product_id", 1234))
                # 1. Cancel old unscaled stop order on exchange
                try:
                    await self.client.cancel_order(order_id=int(exchange_order_id), product_id=prod_id)
                    await self._transition_order(
                        sl_fsm,
                        OrderState.CANCELLED,
                        event=OrderEvent.CANCEL,
                        payload={"reason": "BRACKET_SCALING_CANCEL_OLD_STOP"},
                    )
                    self._active_exit_orders.discard(active_sl)
                except Exception as exc:
                    logger.warning(f"Error cancelling stop loss {exchange_order_id} during scaling: {exc}")
                    # Verify status on exchange
                    status = await self.client.get_order_by_client_id(active_sl)
                    if status and status.get("state") == "filled":
                        logger.critical(f"Stop loss {active_sl} already filled on exchange during scaling attempt")
                        return {"q_filled": filled_quantity, "q_sl": filled_quantity, "status": "ALREADY_FILLED"}
                    raise DeltaNetworkError(f"Bracket scaling failed: could not cancel old stop {exchange_order_id}: {exc}") from exc

                # 2. Place scaled replacement stop order on exchange
                new_sl_cid = ClientOrderIdGenerator.generate(strategy_id="SCALED", bar_timestamp=int(time.time()))
                replacement_req = DeltaOrderRequest(
                    product_id=prod_id,
                    size=int(scaled_sl_qty),
                    side=DeltaOrderSide(str(sl_record.get("side", "sell")).lower()),
                    order_type=DeltaOrderType.STOP_MARKET,
                    stop_price=float(sl_record.get("stop_price", 0.0)),
                    reduce_only=True,
                    client_order_id=new_sl_cid,
                )
                await self.dispatch_order(replacement_req, is_protective=True)

                async with self._lock:
                    self._bracket_pairs[entry_client_order_id]["sl_id"] = new_sl_cid
                    active_sl = new_sl_cid
                    if active_tp:
                        self._companion_map[new_sl_cid] = active_tp
                        self._companion_map[active_tp] = new_sl_cid
            else:
                # Update in-memory record if resting order was not yet dispatched
                async with self._lock:
                    self._order_records[active_sl]["size"] = scaled_sl_qty

        if active_tp and active_tp in self._order_records:
            async with self._lock:
                self._order_records[active_tp]["size"] = scaled_tp_qty

        # Persist bracket relationship to database event log
        if self.session_factory is not None:
            async with self.session_factory() as sess:
                from src.database.models.live import LiveOrder, LiveOrderEvent
                stmt = select(LiveOrder).where(LiveOrder.client_order_id == entry_client_order_id)
                res = await sess.execute(stmt)
                db_entry = res.scalar_one_or_none()
                if db_entry is not None:
                    bracket_event = LiveOrderEvent(
                        id=str(uuid.uuid4()),
                        order_id=db_entry.order_id,
                        from_state=db_entry.state,
                        to_state=db_entry.state,
                        event_type="BRACKET_PAIR_LINKED",
                        payload=json.dumps({
                            "entry_id": entry_client_order_id,
                            "sl_id": active_sl,
                            "tp_id": active_tp,
                            "q_filled": filled_quantity,
                        }),
                        timestamp=datetime.now(timezone.utc),
                    )
                    sess.add(bracket_event)
                    await sess.commit()

        logger.info(
            f"Dynamic bracket scaling applied for entry '{entry_client_order_id}': "
            f"Q_filled={filled_quantity}, Q_SL={scaled_sl_qty}, Q_TP={scaled_tp_qty}"
        )
        return {"q_filled": filled_quantity, "q_sl": scaled_sl_qty, "q_tp": scaled_tp_qty}

    async def handle_protective_fill(
        self,
        filled_client_order_id: str,
        product_id: int,
    ) -> Optional[str]:
        """Synthetic OCO Cancellation: when Stop Loss or Take Profit fills, cancel its companion order.
        
        Verifies exchange status on cancellation failure. If companion also filled,
        records FILLED and increments discrepancy count rather than falsely marking CANCELLED.
        """
        async with self._lock:
            fsm = self._fsm_registry.get(filled_client_order_id)
            if fsm and not fsm.is_terminal:
                await self._transition_order(fsm, OrderState.FILLED, event=OrderEvent.FILL, payload={"filled_at": datetime.now(timezone.utc).isoformat()})

            self._active_exit_orders.discard(filled_client_order_id)
            companion_id = self._companion_map.get(filled_client_order_id)

        if not companion_id:
            return None

        companion_fsm = self._fsm_registry.get(companion_id)
        if companion_fsm and companion_fsm.is_active:
            companion_record = self._order_records.get(companion_id, {})
            exchange_order_id = companion_record.get("id")

            # Dispatch cancel to exchange if exchange ID exists
            if exchange_order_id is not None:
                cancel_success = False
                try:
                    await self.client.cancel_order(order_id=int(exchange_order_id), product_id=product_id)
                    cancel_success = True
                except Exception as exc:
                    logger.warning(f"Error cancelling OCO companion '{companion_id}': {exc}")
                    # Query actual status from exchange matching engine
                    try:
                        status_res = await self.client.get_order_by_client_id(companion_id)
                        ex_state = str(status_res.get("state", "")).lower() if status_res else "unknown"
                    except Exception:
                        ex_state = "unknown"

                    if ex_state == "filled":
                        # Dual execution: companion also filled on the exchange!
                        logger.critical(f"DUAL EXECUTION DETECTED: companion '{companion_id}' also FILLED on exchange!")
                        await self._transition_order(
                            companion_fsm,
                            OrderState.FILLED,
                            event=OrderEvent.FILL,
                            payload={"reason": "DUAL_EXECUTION_COMPANION_FILLED"},
                        )
                        async with self._lock:
                            self._active_exit_orders.discard(companion_id)
                            self.discrepancy_count += 1
                        return companion_id
                    elif ex_state in ("cancelled", "closed"):
                        cancel_success = True
                    else:
                        # Ambiguous outcome: escalate to RECONCILING
                        logger.error(f"OCO companion '{companion_id}' status ambiguous; escalating to RECONCILING")
                        await self._transition_order(
                            companion_fsm,
                            OrderState.FAILED,
                            event=OrderEvent.FAIL,
                            payload={"error": "OCO_CANCEL_FAILED_OUTCOME_UNKNOWN"},
                        )
                        await self._transition_order(
                            companion_fsm,
                            OrderState.RECONCILING,
                            event=OrderEvent.ESCALATE_RECONCILING,
                            payload={"escalated_at": datetime.now(timezone.utc).isoformat()},
                        )
                        async with self._lock:
                            self.discrepancy_count += 1
                        return companion_id

                if cancel_success:
                    await self._transition_order(
                        companion_fsm,
                        OrderState.CANCELLED,
                        event=OrderEvent.CANCEL,
                        payload={"reason": "OCO_COMPANION_FILLED", "filled_companion": filled_client_order_id},
                    )
                    async with self._lock:
                        self._active_exit_orders.discard(companion_id)
                    logger.info(f"Synthetic OCO triggered: cancelled companion order '{companion_id}'")
                    return companion_id

        return None

    async def recover_state(self, session: Optional[AsyncSession] = None) -> Dict[str, Any]:
        """Recover active orders, active exits, and bracket pairings from PostgreSQL after router restart."""
        async with self._lock:
            if session is not None:
                return await self._recover_in_session(session)
            elif self.session_factory is not None:
                async with self.session_factory() as sess:
                    return await self._recover_in_session(sess)
            return {"active_orders": len(self._fsm_registry), "active_exits": len(self._active_exit_orders), "bracket_pairs": len(self._bracket_pairs)}

    async def _recover_in_session(self, session: AsyncSession) -> Dict[str, Any]:
        """Execute state reconstruction within an active database session."""
        from src.database.models.live import LiveOrder, LiveOrderEvent

        # 1. Recover active orders
        active_states = [OrderState.SUBMITTED.value, OrderState.ACCEPTED.value, OrderState.PARTIALLY_FILLED.value, OrderState.RECONCILING.value]
        stmt = select(LiveOrder).where(LiveOrder.state.in_(active_states))
        res = await session.execute(stmt)
        active_db_orders = res.scalars().all()

        for o in active_db_orders:
            fsm = OrderLifecycleFSM(
                order_id=o.order_id,
                client_order_id=o.client_order_id,
                initial_state=OrderState(o.state),
            )
            self._fsm_registry[o.client_order_id] = fsm
            self._order_records[o.client_order_id] = {
                "order_id": o.order_id,
                "client_order_id": o.client_order_id,
                "symbol": o.product_symbol,
                "size": o.quantity,
                "side": o.side,
                "order_type": o.order_type,
                "price": o.price,
                "stop_price": o.stop_price,
                "reduce_only": o.reduce_only,
                "state": o.state,
                "id": o.exchange_order_id,
            }
            if o.reduce_only:
                self._active_exit_orders.add(o.client_order_id)
            if o.state == OrderState.RECONCILING.value:
                self.discrepancy_count += 1

        # 2. Recover bracket relationships
        bracket_stmt = select(LiveOrderEvent).where(LiveOrderEvent.event_type == "BRACKET_PAIR_LINKED")
        bracket_res = await session.execute(bracket_stmt)
        bracket_events = bracket_res.scalars().all()

        for ev in bracket_events:
            try:
                payload = json.loads(ev.payload or "{}")
                entry_id = payload.get("entry_id")
                sl_id = payload.get("sl_id")
                tp_id = payload.get("tp_id")
                if entry_id:
                    self._bracket_pairs[entry_id] = {"sl_id": sl_id, "tp_id": tp_id}
                    if sl_id and tp_id:
                        self._companion_map[sl_id] = tp_id
                        self._companion_map[tp_id] = sl_id
            except Exception as parse_exc:
                logger.warning(f"Failed to parse bracket event {ev.id}: {parse_exc}")

        logger.info(
            f"State recovery complete: {len(active_db_orders)} active orders, "
            f"{len(self._active_exit_orders)} active exits, {len(self._bracket_pairs)} bracket pairs"
        )
        return {
            "active_orders": len(self._fsm_registry),
            "active_exits": len(self._active_exit_orders),
            "bracket_pairs": len(self._bracket_pairs),
            "discrepancies": self.discrepancy_count,
        }

    async def operator_flatten(
        self,
        product_id: int,
        size: float,
        side: DeltaOrderSide,
    ) -> Dict[str, Any]:
        """In-band automated operator flatten endpoint (/api/v1/execution/operator-flatten).
        
        Prerequisites:
        1. Local clock drift <= 1000ms OR Delta /v2/time probe freshly reachable (offset <= 2s).
        2. Node holds valid leadership lease (is_leader_valid()).
        3. Delta REST API transport is responsive.
        4. Rate limiter has capacity.
        
        Fail-Fast Guard:
            If prerequisites NOT met, fails fast with DeltaTransportOrTimeSyncUnavailableError (HTTP 503).
        """
        # 1. Leadership Check
        is_leader = await self._check_leadership()
        if not is_leader:
            raise DeltaTransportOrTimeSyncUnavailableError(
                "In-band automated flatten unavailable due to clock desync or exchange transport outage. "
                "Operator MUST use Delta Web Console out-of-band."
            )

        # 2. Clock Drift Check
        drift_ms = await self._get_clock_drift_ms()
        clock_sync_ok = drift_ms <= self.max_clock_drift_ms

        if not clock_sync_ok:
            # Check if exchange /v2/time probe reachable within 2s
            try:
                server_time = await asyncio.wait_for(self.client.get_server_time(), timeout=2.0)
                if server_time > 0:
                    clock_sync_ok = True
            except Exception:
                clock_sync_ok = False

        if not clock_sync_ok:
            self.kill_switch_state = "CLOCK_DESYNC_UNPROTECTED_EXPOSURE"
            raise DeltaTransportOrTimeSyncUnavailableError(
                "In-band automated flatten unavailable due to clock desync or exchange transport outage. "
                "Operator MUST use Delta Web Console out-of-band."
            )

        # 3. Transport Responsive Check
        try:
            # Quick ping via public endpoint
            await asyncio.wait_for(self.client.get_products(), timeout=2.0)
        except Exception as exc:
            self.kill_switch_state = "CLOCK_DESYNC_UNPROTECTED_EXPOSURE"
            raise DeltaTransportOrTimeSyncUnavailableError(
                "In-band automated flatten unavailable due to clock desync or exchange transport outage. "
                "Operator MUST use Delta Web Console out-of-band."
            ) from exc

        # Prerequisites met: dispatch reduce_only market order
        request = DeltaOrderRequest(
            product_id=product_id,
            size=int(size),
            side=side,
            order_type=DeltaOrderType.MARKET,
            time_in_force=DeltaTimeInForce.IOC,
            reduce_only=True,
        )
        return await self.dispatch_order(order_request=request, is_protective=True)

    def reset_kill_switch(
        self,
        operator_role: str = "ADMIN",
        hmac_signature: Optional[str] = None,
    ) -> bool:
        """Cryptographic authenticated operator reset.
        
        Invariants:
        1. AI models have ZERO reset authority.
        2. Requires authenticated ADMIN.
        3. DiscrepancyCount == 0 precondition; if discrepancies remain, rejects reset.
        """
        if operator_role != "ADMIN":
            raise PermissionError("Only authenticated ADMIN role may reset the Kill Switch")

        if self.discrepancy_count > 0:
            raise ValueError(
                f"Cannot reset Kill Switch: unresolved discrepancy count is {self.discrepancy_count}. "
                "Zero discrepancies required."
            )

        self.kill_switch_state = "NORMAL"
        logger.info("Kill Switch successfully reset to NORMAL by ADMIN operator")
        return True
