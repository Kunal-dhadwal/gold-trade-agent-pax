"""Unit tests for Institutional Twelve-Gate LiveRiskEngine (Sub-Milestone 4.2.5).

Verifies:
1. Pre-flight bootstrap assertion (LIVE_TRADING_ENABLED == True required for live dispatch).
2. All twelve (12) deterministic mathematical live risk gates independently:
   - Gate 1: Peak-to-Trough Drawdown Cap (>= 6.0%) trips Kill Switch to FLATTENING.
   - Gate 2: Daily Max Loss Limit (>= 3.0% from 00:00 UTC) with UTC rollover.
   - Gate 3: Dollar-Risk Sizing Clamping (1.0% equity) with lot boundary rounding and fail-closed safety.
   - Gate 4: Leverage & Notional Cap (5.0x equity) with clamping.
   - Gate 5: Single Strategy Lock (active positions >= 1).
   - Gate 6: Consecutive Loss Streak Guard (>= 3 trades -> 2-hour cooldown).
   - Gate 7: Market Regime Filter (whitelist enforcement).
   - Gate 8: Feed Latency & Stale Bar (> 5.0s ceiling, missing timestamp fail-closed).
   - Gate 9: Server Clock Drift SLA (> 1000ms mandatory ceiling).
   - Gate 10: Spread & Slippage Ceiling (> $1.50 or > 2.0x rolling 20-period median).
   - Gate 11: Fee & Metadata Completeness (unverified or > 24h stale cache).
   - Gate 12: Desynchronization & Burst Rejection Circuit Breaker (drift != 0 or >= 3 rejections in 10m).
3. Separation of controls: Protective exit bypass rules vs mandatory unbypassable gates.
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import pytest

from src.core.config import settings
from src.execution.delta.types import (
    DeltaOrderRequest,
    DeltaOrderSide,
    DeltaOrderType,
    DeltaProductSpecification,
    DeltaTimeInForce,
)
from src.risk.high_water_mark import HighWaterMarkManager
from src.risk.live_kill_switch import LiveKillSwitchFSM, LiveKillSwitchState
from src.risk.live_risk_engine import (
    LiveRiskEngine,
    LiveTradingDisabledError,
    assert_live_trading_enabled,
)


@pytest.fixture
def product_spec() -> DeltaProductSpecification:
    """Standard PAXGUSDT product specification."""
    return DeltaProductSpecification(
        id=1001,
        symbol="PAXGUSDT",
        contract_type="perpetual_futures",
        quoting_asset="USDT",
        settling_asset="USDT",
        contract_value=0.001,  # 0.001 oz
        tick_size=0.10,
        min_order_size=1,  # 1 contract
        max_order_size=10000,
        trading_status="operational",
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0005,
    )



@pytest.fixture
def engine(product_spec) -> LiveRiskEngine:
    """Instantiate a LiveRiskEngine with in-memory HWM and KillSwitch for testing."""
    ks = LiveKillSwitchFSM()
    hwm = HighWaterMarkManager()
    eng = LiveRiskEngine(
        account_id="LIVE_ACC_TEST_001",
        kill_switch=ks,
        high_water_mark_manager=hwm,
        product_spec=product_spec,
    )
    # Set initial healthy state
    eng.current_equity = 10000.0
    eng.start_of_day_equity = 10000.0
    eng.current_utc_date = datetime.now(timezone.utc).date()
    hwm._cached_peaks["LIVE_ACC_TEST_001"] = 10000.0
    eng.current_mark_price = 2650.0
    eng.last_feed_timestamp = datetime.now(timezone.utc)
    eng.last_clock_drift_ms = 15.0
    eng.last_spread = 0.50
    for s in [0.40, 0.45, 0.50, 0.55] * 5:
        eng.spread_history.append(s)
    eng.fee_schedule_verified = True
    eng.fee_schedule_timestamp = datetime.now(timezone.utc)
    eng.current_regime = "NORMAL"
    return eng


def make_entry_order(size: int = 10, limit_price: float = 2650.0, sl_price: float = 2640.0) -> DeltaOrderRequest:
    """Helper to create a standard limit entry order with attached bracket."""
    return DeltaOrderRequest(
        product_id=1001,
        size=size,
        side=DeltaOrderSide.BUY,
        order_type=DeltaOrderType.LIMIT,
        limit_price=limit_price,
        stop_loss_price=sl_price,
        take_profit_price=2670.0,
        reduce_only=False,
    )


def make_protective_order(size: int = 10) -> DeltaOrderRequest:
    """Helper to create a reduce-only protective stop order."""
    return DeltaOrderRequest(
        product_id=1001,
        size=size,
        side=DeltaOrderSide.SELL,
        order_type=DeltaOrderType.STOP_MARKET,
        stop_price=2640.0,
        reduce_only=True,
    )


# ------------------------------------------------------------------------------
# 1. PRE-FLIGHT BOOTSTRAP ASSERTION
# ------------------------------------------------------------------------------

def test_preflight_bootstrap_assertion_fails_closed_by_default():
    """Verify assert_live_trading_enabled fails closed when LIVE_TRADING_ENABLED is False."""
    assert settings.LIVE_TRADING_ENABLED is False
    with pytest.raises(LiveTradingDisabledError) as exc_info:
        assert_live_trading_enabled()
    assert "FAIL-CLOSED BOOTSTRAP ASSERTION" in str(exc_info.value)


def test_evaluate_for_dispatch_rejects_when_live_trading_disabled(engine):
    """Verify evaluate_for_dispatch returns PRE_FLIGHT_BOOTSTRAP rejection."""
    order = make_entry_order()
    res = engine.evaluate_for_dispatch({"order_request": order})
    assert res.approved is False
    assert res.failed_gate == "PRE_FLIGHT_BOOTSTRAP"
    assert "LIVE_TRADING_ENABLED is False" in res.message


def test_preflight_bootstrap_passes_when_mocked_true(engine):
    """Verify pre-flight assertion passes when explicitly enabled under mock."""
    order = make_entry_order()
    with patch.object(settings, "LIVE_TRADING_ENABLED", True):
        res = engine.evaluate_for_dispatch({"order_request": order})
        assert res.approved is True


# ------------------------------------------------------------------------------
# 2. TWELVE DETERMINISTIC LIVE RISK GATES
# ------------------------------------------------------------------------------

def test_gate_1_drawdown_cap_breach_trips_flattening(engine):
    """Verify Gate 1: Drawdown >= 6.0% trips LiveKillSwitchFSM into FLATTENING."""
    order = make_entry_order()
    # Peak is 10,000; drop to 9,390 -> 6.1% drawdown
    res = engine.evaluate_order({
        "order_request": order,
        "current_equity": 9390.0,
    })

    assert res.approved is False
    assert res.failed_gate == 1
    assert "Gate 1 Peak-to-Trough Drawdown Breach" in res.message
    assert res.gate_1_drawdown_breach is True
    # Invariant: Trips Kill Switch to FLATTENING
    assert engine.kill_switch.state == LiveKillSwitchState.FLATTENING
    assert engine.kill_switch.policy == "DRAWDOWN_FLATTEN"


def test_gate_1_clean_under_threshold(engine):
    """Verify Gate 1 passes when drawdown is 5.9% (< 6.0%)."""
    order = make_entry_order()
    # Peak equity is 10,000; start of today is 9,500.
    # Current equity is 9,410:
    # Drawdown from peak: (10000 - 9410) / 10000 = 5.9% (< 6.0% -> Gate 1 passes)
    # Daily loss today: (9500 - 9410) / 9500 = 0.95% (< 3.0% -> Gate 2 passes)
    engine.start_of_day_equity = 9500.0
    res = engine.evaluate_order({
        "order_request": order,
        "current_equity": 9410.0,
    })
    assert res.approved is True
    assert engine.kill_switch.state == LiveKillSwitchState.NORMAL



def test_gate_2_daily_loss_limit_breach(engine):
    """Verify Gate 2: Daily loss >= 3.0% blocks new entries for remainder of UTC day."""
    order = make_entry_order()
    # Start of day equity 10,000; current equity 9,690 -> 3.1% daily loss
    res = engine.evaluate_order({
        "order_request": order,
        "current_equity": 9690.0,
    })
    assert res.approved is False
    assert res.failed_gate == 2
    assert "Gate 2 Daily Max Loss Breach" in res.message


def test_gate_2_utc_midnight_rollover(engine):
    """Verify Gate 2 start-of-day equity resets at UTC midnight."""
    order = make_entry_order()
    engine.start_of_day_equity = 10000.0
    # Simulate equity drop to 9750 (2.5% loss on day 1)
    engine.current_equity = 9750.0

    # Rollover to tomorrow
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    engine.current_utc_date = tomorrow - timedelta(days=1)  # old date
    engine._check_and_rollover_daily_equity(9750.0, datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc))

    # New baseline start_of_day_equity is now 9750.0
    assert engine.start_of_day_equity == 9750.0
    assert engine.current_utc_date == tomorrow
    # Equity at 9750 has 0.0% loss on new day
    assert engine.calculate_daily_loss_pct(9750.0) == 0.0


def test_gate_3_sizing_clamping_formula(engine):
    """Verify Gate 3: 1.0% dollar risk clamps size down to contract lot boundaries.

    Formula:
        Equity = $10,000.0
        MaxDollarRisk = $10,000 * 0.010 = $100.0
        Entry = 2650.0, SL = 2640.0 -> StopDistance = $10.0
        ContractValue = 0.001
        Q_max_risk = 100.0 / (10.0 * 0.001) = 100.0 / 0.01 = 10,000 lots
    """
    # If requested is 15,000 lots (> 10,000), it should be clamped to 10,000
    order = make_entry_order(size=15000, limit_price=2650.0, sl_price=2640.0)
    res = engine.evaluate_order({"order_request": order})

    assert res.approved is True
    assert res.clamped_size == 10000
    assert order.size == 10000


def test_gate_3_sizing_fail_closed_missing_sl(engine):
    """Verify Gate 3 rejects fail-closed if stop loss price is missing."""
    order = make_entry_order()
    order.stop_loss_price = None
    res = engine.evaluate_order({"order_request": order})
    assert res.approved is False
    assert res.failed_gate == 3
    assert "Missing stop loss price" in res.message


def test_gate_3_sizing_fail_closed_zero_stop_distance(engine):
    """Verify Gate 3 rejects fail-closed if stop distance is zero."""
    order = make_entry_order(limit_price=2650.0, sl_price=2650.0)
    res = engine.evaluate_order({"order_request": order})
    assert res.approved is False
    assert res.failed_gate == 3
    assert "Invalid zero stop distance" in res.message


def test_gate_3_sizing_fail_closed_below_minimum_order_size(engine):
    """Verify Gate 3 rejects if clamped size is below minimum order size (1 lot)."""
    # Set peak and start of day equity to 100 so Gate 1 and Gate 2 are not breached:
    engine.hwm_manager._cached_peaks["LIVE_ACC_TEST_001"] = 100.0
    engine.start_of_day_equity = 100.0
    # Equity = $100 -> MaxDollarRisk = $1.0
    # StopDistance = $2000, ContractValue = 0.001 -> Q_max_risk = 1.0 / 2.0 = 0.5 lots -> clamped = 0 lots < min 1 lot
    order = make_entry_order(size=5, limit_price=2650.0, sl_price=650.0)
    res = engine.evaluate_order({
        "order_request": order,
        "current_equity": 100.0,
    })
    assert res.approved is False
    assert res.failed_gate == 3
    assert "below minimum order size" in res.message



def test_gate_4_leverage_notional_cap(engine):
    """Verify Gate 4: Clamps order size when notional exceeds 5.0x equity cap."""
    # Equity = $10,000 -> MaxNotional = $50,000
    # Entry = 2650.0, ContractValue = 0.001 -> 1 lot = $2.65 notional
    # 25,000 lots = $66,250 notional (> $50,000)
    # Available = $50,000 -> Q_max = 50,000 / 2.65 = 18,867 lots
    order = make_entry_order(size=25000, limit_price=2650.0, sl_price=2649.9)  # very tight SL so Gate 3 allows it
    res = engine.evaluate_order({"order_request": order})

    assert res.approved is True
    assert res.clamped_size == 18867
    assert order.size == 18867


def test_gate_5_single_strategy_position_lock(engine):
    """Verify Gate 5: Rejects new entries if an active position already exists."""
    order = make_entry_order()
    engine.active_position_count = 1  # 1 open position already

    res = engine.evaluate_order({"order_request": order})
    assert res.approved is False
    assert res.failed_gate == 5
    assert "Gate 5 Single Strategy Lock Rejection" in res.message


def test_gate_6_consecutive_loss_streak_cooldown(engine):
    """Verify Gate 6: 3 consecutive losses triggers 2-hour strategy cooldown."""
    order = make_entry_order()
    now = datetime.now(timezone.utc)

    # Record 3 consecutive losses
    engine.record_trade_result(-100.0, now - timedelta(minutes=30))
    engine.record_trade_result(-80.0, now - timedelta(minutes=20))
    engine.record_trade_result(-50.0, now - timedelta(minutes=10))

    assert engine.consecutive_losses == 3

    # Within 2-hour window: rejected
    res = engine.evaluate_order({"order_request": order})
    assert res.approved is False
    assert res.failed_gate == 6
    assert "Gate 6 Loss Streak Cooldown Rejection" in res.message

    # Fast forward past 2 hours (121 minutes)
    engine.last_loss_timestamp = now - timedelta(minutes=121)
    res_after = engine.evaluate_order({"order_request": order})
    assert res_after.approved is True
    assert engine.consecutive_losses == 0  # reset


def test_gate_7_market_regime_filter(engine):
    """Verify Gate 7: Rejects entries when detected regime is not in whitelist."""
    order = make_entry_order()
    engine.current_regime = "HIGH_VOLATILITY_CRASH"

    res = engine.evaluate_order({"order_request": order})
    assert res.approved is False
    assert res.failed_gate == 7
    assert "Gate 7 Market Regime Filter Rejection" in res.message


def test_gate_8_feed_latency_stale_bar(engine):
    """Verify Gate 8: Feed latency > 5.0s blocks new entries."""
    order = make_entry_order()
    now = datetime.now(timezone.utc)
    engine.last_feed_timestamp = now - timedelta(seconds=6.0)  # 6.0s old

    res = engine.evaluate_order({"order_request": order})
    assert res.approved is False
    assert res.failed_gate == 8
    assert "Gate 8 Stale Bar Rejection" in res.message


def test_gate_8_missing_feed_timestamp_fails_closed(engine):
    """Verify Gate 8 rejects fail-closed if feed timestamp is missing."""
    order = make_entry_order()
    engine.last_feed_timestamp = None

    res = engine.evaluate_order({"order_request": order})
    assert res.approved is False
    assert res.failed_gate == 8
    assert "unavailable (fail-closed)" in res.message


def test_gate_9_clock_drift_sla_breach(engine):
    """Verify Gate 9: Server clock drift > 1000ms rejects order."""
    order = make_entry_order()
    engine.last_clock_drift_ms = 1200.0  # 1200ms drift

    res = engine.evaluate_order({"order_request": order})
    assert res.approved is False
    assert res.failed_gate == 9
    assert "Gate 9 Clock Drift SLA Rejection" in res.message


def test_gate_10_spread_ceiling_absolute(engine):
    """Verify Gate 10: Spread > $1.50 rejects entry."""
    order = make_entry_order()
    engine.last_spread = 1.75  # $1.75 > $1.50

    res = engine.evaluate_order({"order_request": order})
    assert res.approved is False
    assert res.failed_gate == 10
    assert "Gate 10 Spread Ceiling Rejection" in res.message


def test_gate_10_spread_ratio_relative_median(engine):
    """Verify Gate 10: Spread > 2.0x median of rolling 20 rejects entry."""
    order = make_entry_order()
    engine.spread_history.clear()
    for _ in range(20):
        engine.spread_history.append(0.50)  # median = 0.50 -> 2.0x is 1.00

    engine.last_spread = 1.10  # 1.10 <= 1.50 absolute, but > 2.0x 0.50

    res = engine.evaluate_order({"order_request": order})
    assert res.approved is False
    assert res.failed_gate == 10
    assert "Gate 10 Spread Ratio Rejection" in res.message


def test_gate_11_fee_metadata_completeness(engine):
    """Verify Gate 11: Unverified or stale fee schedule rejects new entries."""
    order = make_entry_order()

    # Case 1: Unverified
    engine.fee_schedule_verified = False
    res1 = engine.evaluate_order({"order_request": order})
    assert res1.approved is False
    assert res1.failed_gate == 11
    assert "Fee schedule has not been verified" in res1.message

    # Case 2: Stale (> 24 hours old)
    engine.fee_schedule_verified = True
    now = datetime.now(timezone.utc)
    engine.fee_schedule_timestamp = now - timedelta(hours=25)
    res2 = engine.evaluate_order({"order_request": order})
    assert res2.approved is False
    assert res2.failed_gate == 11
    assert "Fee schedule cache is stale" in res2.message


def test_gate_12_quantity_drift_trips_kill_switch(engine):
    """Verify Gate 12: Quantity drift != 0 trips Kill Switch into MANUAL_RESET_REQUIRED."""
    order = make_entry_order()
    tripped = engine.set_quantity_drift(5.0)  # 5 lots drift

    assert tripped is True
    assert engine.kill_switch.state == LiveKillSwitchState.MANUAL_RESET_REQUIRED
    assert engine.kill_switch.policy == "GHOST_EXPOSURE"

    res = engine.evaluate_order({"order_request": order})
    assert res.approved is False
    # Kill switch active blocks entry
    assert res.failed_gate in ("KILL_SWITCH", 12)


def test_gate_12_burst_rejection_circuit_breaker(engine):
    """Verify Gate 12: >= 3 rejections in 10 minutes trips Live Kill Switch."""
    order = make_entry_order()
    now = datetime.now(timezone.utc)

    # 1st rejection
    engine.record_rejection("Reject 1", now - timedelta(minutes=5))
    assert engine.kill_switch.state == LiveKillSwitchState.NORMAL

    # 2nd rejection
    engine.record_rejection("Reject 2", now - timedelta(minutes=2))
    assert engine.kill_switch.state == LiveKillSwitchState.NORMAL

    # 3rd rejection -> Trips circuit breaker!
    tripped = engine.record_rejection("Reject 3", now)
    assert tripped is True
    assert engine.kill_switch.state == LiveKillSwitchState.MANUAL_RESET_REQUIRED
    assert engine.kill_switch.policy == "BURST_REJECTION"

    res = engine.evaluate_order({"order_request": order})
    assert res.approved is False


# ------------------------------------------------------------------------------
# 3. SEPARATION OF CONTROLS & PROTECTIVE EXIT BYPASS RULES
# ------------------------------------------------------------------------------

def test_protective_exits_bypass_authorized_gates(engine):
    """Verify protective exits (reduce_only) BYPASS Gates 2, 5, 6, 7, 8, 10, 11."""
    # Intentionally trigger all bypassable gates simultaneously
    engine.start_of_day_equity = 10000.0
    engine.current_equity = 9600.0  # Gate 2 daily loss 4.0%
    engine.active_position_count = 1  # Gate 5 position lock
    engine.consecutive_losses = 3  # Gate 6 loss streak cooldown
    engine.last_loss_timestamp = datetime.now(timezone.utc)
    engine.current_regime = "CRASH_REGIME"  # Gate 7 regime filter
    engine.last_feed_timestamp = datetime.now(timezone.utc) - timedelta(seconds=10.0)  # Gate 8 stale bar
    engine.last_spread = 2.50  # Gate 10 spread ceiling
    engine.fee_schedule_verified = False  # Gate 11 fee unverified

    # Create protective exit order (reduce_only: true)
    protective_order = make_protective_order(size=10)

    # Entry order is strictly blocked
    entry_order = make_entry_order()
    res_entry = engine.evaluate_order({"order_request": entry_order})
    assert res_entry.approved is False

    # Protective exit order passes cleanly!
    res_exit = engine.evaluate_order({
        "order_request": protective_order,
        "is_protective": True,
    })
    assert res_exit.approved is True
    assert res_exit.is_protective is True
    assert res_exit.clamped_size == 10


def test_protective_exits_enforce_gate_1_drawdown(engine):
    """Verify protective exits MUST NOT bypass Gate 1 drawdown cap (flags breach)."""
    protective_order = make_protective_order(size=10)
    # Peak is 10,000; drop to 9,300 -> 7.0% drawdown
    res = engine.evaluate_order({
        "order_request": protective_order,
        "current_equity": 9300.0,
        "is_protective": True,
    })

    # Flagged with gate_1_drawdown_breach = True and trips FLATTENING
    assert res.gate_1_drawdown_breach is True
    assert engine.kill_switch.state == LiveKillSwitchState.FLATTENING
    # But order is allowed to execute as reduce_only flatten!
    assert res.approved is True


def test_protective_exits_enforce_gate_9_clock_drift(engine):
    """Verify protective exits MUST NOT bypass Gate 9 clock drift SLA."""
    protective_order = make_protective_order(size=10)
    engine.last_clock_drift_ms = 1500.0  # 1500ms drift

    res = engine.evaluate_order({
        "order_request": protective_order,
        "is_protective": True,
    })
    assert res.approved is False
    assert res.failed_gate == 9
    assert "Gate 9 Clock Drift SLA Breach on Protective Exit" in res.message


def test_protective_exits_enforce_gate_12_quantity_drift(engine):
    """Verify protective exits MUST NOT bypass Gate 12 desynchronization drift."""
    protective_order = make_protective_order(size=10)
    engine.quantity_drift = 2.0  # Drift present

    res = engine.evaluate_order({
        "order_request": protective_order,
        "is_protective": True,
    })
    assert res.approved is False
    assert res.failed_gate == 12
    assert "Gate 12 Quantity Drift Breach on Protective Exit" in res.message
