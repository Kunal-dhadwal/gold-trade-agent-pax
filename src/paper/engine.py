"""Central Paper Trading Engine orchestrating execution, risk, and state events."""

from datetime import datetime, timezone
from enum import Enum
import logging
from typing import Any, Dict, List, Optional
import pandas as pd
from src.paper.matching import MatchingEngine, OrderState, PaperOrder
from src.paper.portfolio import SingleStrategyPortfolio
from src.paper.positions import PositionTracker
from src.paper.specifications import InstrumentSpecification, get_instrument_specification
from src.real_time.event_bus import EventBus, EventEnvelope
from src.real_time.idempotency import IdempotencyEngine
from src.real_time.state_cache import StateCache
from src.risk.paper_risk_engine import PaperRiskEngine
from src.strategy.base import BaseStrategy

logger = logging.getLogger(__name__)


class EngineState(str, Enum):
    """Lifecycle states of the Paper Trading Engine."""

    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    PAUSED_DATA_OUTAGE = "PAUSED_DATA_OUTAGE"
    STOPPED = "STOPPED"
    TERMINATED = "TERMINATED"


class PaperTradingEngine:
    """Coordinates real-time paper execution following canonical Next-Bar Open semantics."""

    def __init__(
        self,
        account_id: str,
        strategy_version_id: str,
        symbol: str = "PAXGUSDT",
        initial_capital: float = 10000.0,
        risk_engine: Optional[PaperRiskEngine] = None,
        matching_engine: Optional[MatchingEngine] = None,
        strategy_interpreter: Optional[BaseStrategy] = None,
        event_bus: Optional[EventBus] = None,
        state_cache: Optional[StateCache] = None,
        idempotency: Optional[IdempotencyEngine] = None,
        session_factory: Optional[Any] = None,
    ):
        self.account_id = account_id
        self.strategy_version_id = strategy_version_id
        self.symbol = symbol.strip().upper()
        self.spec: InstrumentSpecification = get_instrument_specification(self.symbol)

        self.portfolio = SingleStrategyPortfolio(account_id=account_id, initial_capital=initial_capital)
        self.risk_engine = risk_engine or PaperRiskEngine()
        self.matching_engine = matching_engine or MatchingEngine()
        self.strategy_interpreter = strategy_interpreter
        self.event_bus = event_bus
        self.state_cache = state_cache
        self.idempotency = idempotency or IdempotencyEngine()
        if session_factory is not None:
            self.session_factory = session_factory
        else:
            try:
                from src.database.connection import AsyncSessionLocal
                self.session_factory = AsyncSessionLocal
            except Exception:
                self.session_factory = None

        self.position: Optional[PositionTracker] = None
        self.pending_order: Optional[PaperOrder] = None
        self.consecutive_losses = 0
        self.candle_history: List[Dict[str, Any]] = []
        self.trades_history: List[Dict[str, Any]] = []
        self.is_paused: bool = False
        self.is_stopped: bool = False

    async def pause(self) -> None:
        """Pause strategy execution (block new entry signals, keep managing open positions)."""
        self.is_paused = True
        logger.info(f"Strategy {self.strategy_version_id} PAUSED for account {self.account_id}")

    async def resume(self) -> None:
        """Resume strategy execution."""
        self.is_paused = False
        logger.info(f"Strategy {self.strategy_version_id} RESUMED for account {self.account_id}")

    async def stop(self) -> None:
        """Stop strategy execution completely and flatten any open positions."""
        self.is_stopped = True
        await self.flatten_all(reason="STRATEGY_STOPPED", triggered_by="OPERATOR")
        logger.info(f"Strategy {self.strategy_version_id} STOPPED for account {self.account_id}")

    async def flatten_all(self, reason: str = "EMERGENCY_FLATTEN", triggered_by: str = "OPERATOR") -> Dict[str, Any]:
        """Emergency flatten: cancels pending orders, liquidates open position, latches kill switch."""
        orders_canceled = 0
        positions_flattened = 0
        if self.pending_order is not None:
            self.pending_order.transition_to(OrderState.CANCELLED)
            self.pending_order = None
            orders_canceled += 1

        if self.position is not None:
            now = datetime.now(timezone.utc)
            mark = self.position.current_mark_price or self.position.entry_price
            await self._close_position(mark, reason, 0.20, now)
            positions_flattened += 1

        self.risk_engine.kill_switch.trigger(reason=reason, triggered_by=triggered_by)
        return {
            "status": "FLATTENED",
            "positions_flattened": positions_flattened,
            "orders_canceled": orders_canceled,
            "reason": reason,
        }

    async def _persist_order_event_db(
        self,
        order_id: str,
        event_type: str,
        from_state: str,
        to_state: str,
        idempotency_key: str,
        payload: Dict[str, Any],
        dt: datetime,
    ) -> None:
        """Persist order state transition event to PostgreSQL paper_order_events table."""
        if not self.session_factory:
            return
        try:
            from src.database.models.paper import PaperOrderEvent

            async with self.session_factory() as session:
                evt = PaperOrderEvent(
                    order_id=order_id,
                    account_id=self.account_id,
                    event_type=event_type,
                    idempotency_key=idempotency_key,
                    from_state=from_state,
                    to_state=to_state,
                    payload=payload,
                    timestamp=dt,
                )
                session.add(evt)
                await session.commit()
        except Exception as e:
            logger.warning(f"Failed to persist order event to DB: {e}")

    async def _persist_position_fill_db(
        self, fill_price: float, quantity: float, direction: str, dt: datetime, notional: float
    ) -> None:
        """Upsert open paper position in PostgreSQL positions table."""
        if not self.session_factory:
            return
        try:
            from sqlalchemy import select
            from src.database.models.strategies import StrategyVersion
            from src.database.models.trading import Position

            async with self.session_factory() as session:
                sv_stmt = select(StrategyVersion).where(
                    (StrategyVersion.id == self.strategy_version_id)
                    | (StrategyVersion.strategy_id == self.strategy_version_id)
                ).order_by(StrategyVersion.created_at.desc())
                sv_res = await session.execute(sv_stmt)
                sv = sv_res.scalars().first()
                resolved_sv_id = sv.id if sv else self.strategy_version_id

                stmt = select(Position).where(
                    Position.account_id == self.account_id,
                    Position.instrument_id == self.symbol,
                    Position.mode == "PAPER",
                )
                res = await session.execute(stmt)
                pos = res.scalar_one_or_none()
                if not pos:
                    pos = Position(
                        instrument_id=self.symbol,
                        account_id=self.account_id,
                        strategy_version_id=resolved_sv_id,
                        mode="PAPER",
                        direction=direction,
                        quantity=quantity,
                        average_entry_price=fill_price,
                        current_mark_price=fill_price,
                        unrealized_pnl=0.0,
                        allocated_margin=notional / self.portfolio.leverage,
                        updated_at=dt,
                    )
                    session.add(pos)
                else:
                    pos.direction = direction
                    pos.quantity = quantity
                    pos.average_entry_price = fill_price
                    pos.current_mark_price = fill_price
                    pos.unrealized_pnl = 0.0
                    pos.allocated_margin = notional / self.portfolio.leverage
                    pos.updated_at = dt
                await session.commit()
        except Exception as e:
            logger.warning(f"Failed to persist position fill to DB: {e}")

    async def _persist_closed_trade_db(self, trade_record: Dict[str, Any], dt: datetime) -> None:
        """Persist closed trade record and update account/session in PostgreSQL."""
        if not self.session_factory:
            return
        try:
            from sqlalchemy import delete, select
            from src.database.models.paper import PaperAccount, PaperTradingSession
            from src.database.models.strategies import StrategyVersion
            from src.database.models.trading import PaperTrade, Position

            async with self.session_factory() as session:
                # 0. Resolve foreign key for StrategyVersion
                sv_stmt = select(StrategyVersion).where(
                    (StrategyVersion.id == self.strategy_version_id)
                    | (StrategyVersion.strategy_id == self.strategy_version_id)
                ).order_by(StrategyVersion.created_at.desc())
                sv_res = await session.execute(sv_stmt)
                sv = sv_res.scalars().first()
                resolved_sv_id = sv.id if sv else self.strategy_version_id

                # 1. Insert PaperTrade
                pt = PaperTrade(
                    account_id=self.account_id,
                    strategy_version_id=resolved_sv_id,
                    instrument_id=self.symbol,
                    direction=trade_record["direction"],
                    order_type="MARKET",
                    entry_time=datetime.fromisoformat(trade_record["entry_time"]),
                    exit_time=datetime.fromisoformat(trade_record["exit_time"]),
                    entry_price=trade_record["entry_price"],
                    exit_price=trade_record["exit_price"],
                    size=trade_record["quantity"],
                    stop_loss=float(trade_record.get("stop_loss", trade_record["entry_price"] * 0.99)),
                    take_profit=float(trade_record.get("take_profit", trade_record["entry_price"] * 1.02)),
                    realized_pnl=trade_record["net_pnl"],
                    fees=trade_record["total_fees"],
                    slippage=float(trade_record.get("slippage", 0.20)),
                    status="CLOSED",
                    idempotency_key=IdempotencyEngine.build_fill_key(str(len(self.trades_history)), dt.isoformat()),
                    mae_pct=trade_record.get("mae_pct"),
                    mfe_pct=trade_record.get("mfe_pct"),
                )
                session.add(pt)

                # 2. Clear Position row
                del_stmt = delete(Position).where(
                    Position.account_id == self.account_id,
                    Position.instrument_id == self.symbol,
                    Position.mode == "PAPER",
                )
                await session.execute(del_stmt)

                # 3. Update PaperAccount row
                acc_stmt = select(PaperAccount).where(PaperAccount.id == self.account_id)
                acc_res = await session.execute(acc_stmt)
                acc = acc_res.scalar_one_or_none()
                if acc:
                    acc.current_cash = self.portfolio.cash_balance
                    acc.peak_equity = self.portfolio.peak_equity
                    acc.max_drawdown_pct = self.portfolio.get_max_drawdown_pct()

                # 4. Update active PaperTradingSession if any
                sess_stmt = select(PaperTradingSession).where(
                    PaperTradingSession.account_id == self.account_id,
                    PaperTradingSession.status == "PAPER_ACTIVE",
                )
                sess_res = await session.execute(sess_stmt)
                sess = sess_res.scalar_one_or_none()
                if sess:
                    sess.total_trades += 1
                    if trade_record["net_pnl"] > 0:
                        sess.winning_trades += 1
                        sess.gross_profit += trade_record["gross_pnl"]
                    else:
                        sess.losing_trades += 1
                        sess.gross_loss += abs(trade_record["gross_pnl"])
                    sess.realized_pnl += trade_record["net_pnl"]
                    sess.max_drawdown_pct = max(sess.max_drawdown_pct, self.portfolio.get_max_drawdown_pct())

                await session.commit()
        except Exception as e:
            logger.warning(f"Failed to persist closed trade to DB: {e}")

    async def _persist_equity_snapshot_db(self, dt: datetime, unrealized: float) -> None:
        """Persist periodic equity snapshot and update account in PostgreSQL."""
        if not self.session_factory:
            return
        try:
            from sqlalchemy import select
            from src.database.models.paper import PaperAccount, PaperEquitySnapshot

            async with self.session_factory() as session:
                snap = PaperEquitySnapshot(
                    account_id=self.account_id,
                    timestamp=dt,
                    equity=self.portfolio.get_equity(unrealized),
                    cash=self.portfolio.cash_balance,
                    used_margin=self.portfolio.reserved_margin,
                    unrealized_pnl=unrealized,
                    drawdown_pct=self.portfolio.get_drawdown_pct(unrealized),
                )
                session.add(snap)

                acc_stmt = select(PaperAccount).where(PaperAccount.id == self.account_id)
                acc_res = await session.execute(acc_stmt)
                acc = acc_res.scalar_one_or_none()
                if acc:
                    acc.current_cash = self.portfolio.cash_balance
                    acc.peak_equity = self.portfolio.peak_equity
                    acc.max_drawdown_pct = max(acc.max_drawdown_pct, self.portfolio.get_drawdown_pct(unrealized))

                await session.commit()
        except Exception as e:
            logger.warning(f"Failed to persist equity snapshot to DB: {e}")


    async def on_candle_closed(self, candle: Dict[str, Any]) -> None:
        """Core event loop triggered on every completed 5m candle."""
        if self.is_stopped:
            return

        c_open = float(candle["open"])
        c_high = float(candle["high"])
        c_low = float(candle["low"])
        c_close = float(candle["close"])
        c_time = candle.get("timestamp", datetime.now(timezone.utc).isoformat())

        # Estimated spread (default $0.20 or from candle metadata)
        spread = float(candle.get("spread", 0.20))
        self.risk_engine.update_spread_history(spread)

        # Parse timestamp for UTC daily reset
        if isinstance(c_time, str):
            try:
                dt = datetime.fromisoformat(c_time)
            except Exception:
                dt = datetime.now(timezone.utc)
        else:
            dt = datetime.now(timezone.utc)

        unrealized = self.position.calculate_unrealized_pnl(self.spec) if self.position else 0.0
        self.portfolio.check_daily_reset(dt, unrealized_pnl=unrealized)

        # -------------------------------------------------------------
        # STEP 1: Execute Pending Order (Canonical Next-Bar Open Model)
        # -------------------------------------------------------------
        if self.pending_order is not None and self.position is None:
            await self._fill_pending_order(candle, c_open, spread, dt)

        # -------------------------------------------------------------
        # STEP 2: Evaluate Open Position Exits (Bracket SL / TP)
        # -------------------------------------------------------------
        if self.position is not None:
            self.position.update_mark(c_close, c_high, c_low)
            exit_hit, exit_reason, raw_exit_p = self.position.check_bracket_exit(c_high, c_low)
            if exit_hit:
                await self._close_position(raw_exit_p, exit_reason or "BRACKET_EXIT", spread, dt)

        # -------------------------------------------------------------
        # STEP 3: Signal Generation at Bar Close (if Flat and not paused)
        # -------------------------------------------------------------
        self.candle_history.append(candle)
        if len(self.candle_history) > 500:
            self.candle_history.pop(0)

        if not self.is_paused and self.position is None and self.pending_order is None and self.strategy_interpreter:
            await self._evaluate_strategy_signal(c_close, spread, dt)


        # -------------------------------------------------------------
        # STEP 4: State Synchronization & Events
        # -------------------------------------------------------------
        await self._sync_state_and_events(candle, dt)

    async def _fill_pending_order(
        self, candle: Dict[str, Any], open_price: float, spread: float, dt: datetime
    ) -> None:
        """Fill queued pending order at Next-Bar Open."""
        order = self.pending_order
        assert order is not None

        fill_price, half_spread, slip, fee = self.matching_engine.calculate_entry_fill(
            quote_price=open_price,
            direction=order.direction,
            quantity=order.quantity,
            spread=spread,
            spec=self.spec,
        )

        order.execution_price = fill_price
        order.fee = fee
        order.slippage = slip
        order.transition_to(OrderState.FILLED)

        notional = self.spec.calculate_notional(fill_price, order.quantity)
        self.portfolio.reserve_margin(notional)

        self.position = PositionTracker(
            symbol=self.symbol,
            direction=order.direction,
            quantity=order.quantity,
            entry_price=fill_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            allocated_margin=notional / self.portfolio.leverage,
            entry_fee=fee,
            entry_slippage=slip,
            opened_at=dt,
        )

        logger.info(
            f"FILLED Entry Order {order.order_id}: {order.direction} {order.quantity} {self.symbol} @ ${fill_price:.2f}"
        )

        fill_key = IdempotencyEngine.build_fill_key(order.order_id, dt.isoformat())
        await self._persist_order_event_db(
            order_id=order.order_id,
            event_type="ORDER_FILLED",
            from_state="ACCEPTED",
            to_state="FILLED",
            idempotency_key=fill_key,
            payload={
                "order_id": order.order_id,
                "account_id": self.account_id,
                "symbol": self.symbol,
                "direction": order.direction,
                "fill_price": fill_price,
                "quantity": order.quantity,
                "fee": fee,
                "slippage": slip,
            },
            dt=dt,
        )
        await self._persist_position_fill_db(fill_price, order.quantity, order.direction, dt, notional)

        if self.event_bus:
            envelope = EventEnvelope(
                event_type="ORDER_FILLED",
                aggregate_id=self.account_id,
                idempotency_key=fill_key,
                payload={
                    "order_id": order.order_id,
                    "account_id": self.account_id,
                    "symbol": self.symbol,
                    "direction": order.direction,
                    "fill_price": fill_price,
                    "quantity": order.quantity,
                    "fee": fee,
                    "slippage": slip,
                },
            )
            await self.event_bus.publish(f"order.filled.{self.account_id}", envelope)

        self.pending_order = None

    async def _close_position(self, raw_exit_price: float, reason: str, spread: float, dt: datetime) -> None:
        """Close active position, compute P&L, fees, and settle portfolio."""
        pos = self.position
        assert pos is not None

        fill_exit, half_spread, slip_exit, fee_exit = self.matching_engine.calculate_exit_fill(
            quote_price=raw_exit_price,
            direction=pos.direction,
            quantity=pos.quantity,
            spread=spread,
            spec=self.spec,
        )

        mult = self.spec.contract_size
        if pos.direction == "LONG":
            gross_pnl = (fill_exit - pos.entry_price) * pos.quantity * mult
        else:
            gross_pnl = (pos.entry_price - fill_exit) * pos.quantity * mult

        total_fees = pos.total_fees + fee_exit
        net_pnl = gross_pnl - total_fees

        notional_entry = self.spec.calculate_notional(pos.entry_price, pos.quantity)
        self.portfolio.release_margin(notional_entry)
        self.portfolio.settle_trade(net_pnl=net_pnl, fees=total_fees)

        if net_pnl > 0:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

        trade_record = {
            "account_id": self.account_id,
            "strategy_version_id": self.strategy_version_id,
            "symbol": self.symbol,
            "direction": pos.direction,
            "entry_time": pos.opened_at.isoformat(),
            "exit_time": dt.isoformat(),
            "entry_price": pos.entry_price,
            "exit_price": fill_exit,
            "quantity": pos.quantity,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "total_fees": total_fees,
            "exit_reason": reason,
            "holding_bars": pos.holding_bars,
            "mae_pct": pos.max_mae_pct,
            "mfe_pct": pos.max_mfe_pct,
        }
        self.trades_history.append(trade_record)
        await self._persist_closed_trade_db(trade_record, dt)

        logger.info(
            f"CLOSED Position: {pos.direction} {pos.quantity} @ ${fill_exit:.2f} ({reason}). Net P&L: ${net_pnl:.2f}"
        )

        if self.event_bus:
            envelope = EventEnvelope(
                event_type="ORDER_FILLED",
                aggregate_id=self.account_id,
                idempotency_key=IdempotencyEngine.build_fill_key(str(len(self.trades_history)), dt.isoformat()),
                payload=trade_record,
            )
            await self.event_bus.publish(f"order.filled.{self.account_id}", envelope)

        self.position = None

    async def _evaluate_strategy_signal(self, close_price: float, spread: float, dt: datetime) -> None:
        """Run Phase 2 Strategy AST on history and evaluate Risk Engine if signal generated."""
        if len(self.candle_history) < 1 or not self.strategy_interpreter:
            return

        df = pd.DataFrame(self.candle_history)
        signals = self.strategy_interpreter.generate_signals(df)

        last_sig: Dict[str, Any] = {}
        if isinstance(signals, pd.DataFrame):
            if signals.empty:
                return
            last_sig = signals.iloc[-1].to_dict()
        elif isinstance(signals, list):
            if not signals:
                return
            raw_sig = signals[-1]
            last_sig = raw_sig.__dict__ if hasattr(raw_sig, "__dict__") and not isinstance(raw_sig, dict) else raw_sig
        else:
            return

        raw_dir = last_sig.get("signal", last_sig.get("direction", 0))
        if isinstance(raw_dir, str):
            sig_dir = 1 if raw_dir.upper() == "LONG" else (-1 if raw_dir.upper() == "SHORT" else 0)
        elif hasattr(raw_dir, "value"):
            sig_dir = 1 if "LONG" in str(raw_dir.value).upper() else (-1 if "SHORT" in str(raw_dir.value).upper() else 0)
        else:
            sig_dir = int(raw_dir)

        if sig_dir == 0:
            return

        direction_str = "LONG" if sig_dir > 0 else "SHORT"
        entry_p = float(last_sig.get("entry_price", close_price))
        sl_p = float(last_sig.get("stop_loss", entry_p * (0.99 if direction_str == "LONG" else 1.01)))
        tp_p = float(last_sig.get("take_profit", entry_p * (1.02 if direction_str == "LONG" else 0.98)))
        requested_qty = float(last_sig.get("size", last_sig.get("suggested_size", 10.0)))


        # Check idempotency
        sig_key = IdempotencyEngine.build_signal_key(self.strategy_version_id, dt.isoformat(), direction_str)
        if await self.idempotency.is_duplicate(sig_key):
            logger.debug(f"Dropping duplicate signal key: {sig_key}")
            return

        # Evaluate 8 Deterministic Risk Gates
        decision = self.risk_engine.evaluate_order(
            account_equity=self.portfolio.get_equity(),
            account_cash=self.portfolio.cash_balance,
            starting_daily_equity=self.portfolio.starting_daily_equity,
            peak_equity=self.portfolio.peak_equity,
            symbol=self.symbol,
            direction=direction_str,
            requested_quantity=requested_qty,
            entry_price=entry_p,
            stop_loss=sl_p,
            current_spread=spread,
            strategy_consecutive_losses=self.consecutive_losses,
        )

        if not decision.approved:
            logger.warning(f"Risk Engine VETOED signal for {direction_str}: {decision.reason}")
            if self.event_bus:
                alert_env = EventEnvelope(
                    event_type="RISK_ALERT",
                    aggregate_id=self.account_id,
                    idempotency_key=f"ALERT:{sig_key}",
                    payload={"gate_failed": decision.gate_failed, "reason": decision.reason},
                )
                await self.event_bus.publish(f"risk.alert.{self.account_id}", alert_env)
            return

        # Queue order for Next-Bar Open
        final_qty = decision.adjusted_quantity or requested_qty
        order = PaperOrder(
            account_id=self.account_id,
            symbol=self.symbol,
            direction=direction_str,
            order_type="MARKET",
            quantity=final_qty,
            requested_price=entry_p,
            stop_loss=sl_p,
            take_profit=tp_p,
            idempotency_key=sig_key,
        )
        order.transition_to(OrderState.SUBMITTED)
        order.transition_to(OrderState.ACCEPTED)
        self.pending_order = order

        logger.info(f"ACCEPTED Next-Bar Open Order {order.order_id}: {direction_str} {final_qty} {self.symbol}")

        await self._persist_order_event_db(
            order_id=order.order_id,
            event_type="ORDER_CREATED",
            from_state="CREATED",
            to_state="ACCEPTED",
            idempotency_key=sig_key,
            payload={
                "order_id": order.order_id,
                "account_id": self.account_id,
                "symbol": self.symbol,
                "direction": direction_str,
                "quantity": final_qty,
                "requested_price": entry_p,
            },
            dt=dt,
        )

        if self.event_bus:
            order_env = EventEnvelope(
                event_type="ORDER_CREATED",
                aggregate_id=self.account_id,
                idempotency_key=order.idempotency_key,
                payload={
                    "order_id": order.order_id,
                    "account_id": self.account_id,
                    "symbol": self.symbol,
                    "direction": direction_str,
                    "quantity": final_qty,
                    "requested_price": entry_p,
                },
            )
            await self.event_bus.publish(f"order.created.{self.account_id}", order_env)

    async def _sync_state_and_events(self, candle: Dict[str, Any], dt: datetime) -> None:
        """Update fast Redis state caches and dispatch periodic updates."""
        unrealized = self.position.calculate_unrealized_pnl(self.spec) if self.position else 0.0
        portfolio_dict = self.portfolio.to_dict(unrealized_pnl=unrealized)

        await self._persist_equity_snapshot_db(dt, unrealized)

        if self.state_cache:
            await self.state_cache.set_account(self.account_id, portfolio_dict)
            if self.position:
                await self.state_cache.set_position(
                    self.account_id,
                    self.symbol,
                    {
                        "symbol": self.symbol,
                        "direction": self.position.direction,
                        "quantity": self.position.quantity,
                        "entry_price": self.position.entry_price,
                        "current_mark_price": self.position.current_mark_price,
                        "unrealized_pnl": unrealized,
                        "stop_loss": self.position.stop_loss,
                        "take_profit": self.position.take_profit,
                        "mae_pct": self.position.max_mae_pct,
                        "mfe_pct": self.position.max_mfe_pct,
                    },
                )
            else:
                await self.state_cache.delete_position(self.account_id, self.symbol)

            await self.state_cache.set_latest_candle(self.symbol, "5m", candle)
            await self.state_cache.set_risk_state(self.account_id, self.risk_engine.kill_switch.get_status())

        if self.event_bus:
            port_env = EventEnvelope(
                event_type="PORTFOLIO_UPDATED",
                aggregate_id=self.account_id,
                idempotency_key=f"PORT:{self.account_id}:{dt.isoformat()}",
                payload=portfolio_dict,
            )
            await self.event_bus.publish(f"portfolio.updated.{self.account_id}", port_env)
