"""Unit tests for PositionTracker and SingleStrategyPortfolio."""

from datetime import datetime, timezone
import pytest
from src.paper.portfolio import SingleStrategyPortfolio
from src.paper.positions import PositionTracker
from src.paper.specifications import get_instrument_specification


def test_position_tracker_excursions():
    dt = datetime.now(timezone.utc)
    pos = PositionTracker(
        symbol="PAXGUSDT",
        direction="LONG",
        quantity=100.0,
        entry_price=2700.0,
        stop_loss=2680.0,
        take_profit=2740.0,
        allocated_margin=270.0,
        opened_at=dt,
    )

    # Price moves favorably to high 2720, low 2695, close 2715
    pos.update_mark(mark_price=2715.0, high=2720.0, low=2695.0)

    # 100 contracts = 0.1 oz. Favorable move +$20 * 0.1 = +$2.00
    assert pos.max_mfe_usd == pytest.approx(2.0, rel=1e-3)
    # Adverse move -$5 * 0.1 = -$0.50 -> adverse excursion 0.50
    assert pos.max_mae_usd == pytest.approx(0.5, rel=1e-3)


def test_position_intra_bar_sl_tp_tiebreaker():
    dt = datetime.now(timezone.utc)
    pos = PositionTracker(
        symbol="PAXGUSDT",
        direction="LONG",
        quantity=100.0,
        entry_price=2700.0,
        stop_loss=2680.0,
        take_profit=2740.0,
        allocated_margin=270.0,
        opened_at=dt,
    )

    # Candle spikes to high 2750 (past TP 2740) AND drops to low 2670 (past SL 2680)
    hit, reason, exit_price = pos.check_bracket_exit(high=2750.0, low=2670.0)

    # Canonical invariant: conservative SL-first tie breaker
    assert hit is True
    assert reason == "STOP_LOSS"
    assert exit_price == 2680.0


def test_single_strategy_portfolio_accounting():
    dt = datetime(2026, 3, 15, 10, 0, tzinfo=timezone.utc)
    port = SingleStrategyPortfolio(account_id="ACC_1", initial_capital=10000.0, leverage=10.0)

    assert port.cash_balance == 10000.0

    assert port.get_equity() == 10000.0
    assert port.get_available_margin() == 10000.0

    # Reserve margin for $2700 notional at 10x leverage -> $270
    port.reserve_margin(notional=2700.0)
    assert port.margin_reserved == 270.0
    assert port.get_available_margin() == 9730.0

    # Settle trade with +$150 profit and margin release
    port.release_margin(notional=2700.0)
    port.settle_trade(net_pnl=150.0, fees=1.5)
    assert port.margin_reserved == 0.0
    assert port.cash_balance == 10150.0
    assert port.peak_equity == 10150.0


    # Test UTC daily reset
    next_day_dt = datetime(2026, 3, 16, 1, 0, tzinfo=timezone.utc)
    port.check_daily_reset(next_day_dt, unrealized_pnl=0.0)
    assert port.starting_daily_equity == 10150.0
