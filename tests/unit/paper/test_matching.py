"""Unit tests for MatchingEngine and PaperOrder state transitions."""

import pytest
from src.paper.matching import MatchingEngine, OrderState, PaperOrder
from src.paper.specifications import get_instrument_specification


def test_paper_order_lifecycle_transitions():
    order = PaperOrder(
        account_id="ACC_1",
        symbol="PAXGUSDT",
        direction="LONG",
        order_type="MARKET",
        quantity=50.0,
        requested_price=2700.0,
    )
    assert order.state == OrderState.CREATED

    order.transition_to(OrderState.SUBMITTED)
    assert order.state == OrderState.SUBMITTED

    order.transition_to(OrderState.ACCEPTED)
    assert order.state == OrderState.ACCEPTED

    order.transition_to(OrderState.FILLED)
    assert order.state == OrderState.FILLED


def test_invalid_order_transition_raises():
    order = PaperOrder(
        account_id="ACC_1",
        symbol="PAXGUSDT",
        direction="LONG",
        order_type="MARKET",
        quantity=50.0,
        requested_price=2700.0,
    )
    order.transition_to(OrderState.SUBMITTED)
    order.transition_to(OrderState.ACCEPTED)
    order.transition_to(OrderState.FILLED)
    with pytest.raises(ValueError):
        order.transition_to(OrderState.CANCELLED)



def test_matching_engine_long_fill():
    engine = MatchingEngine(fixed_slippage_usd=0.15)
    spec = get_instrument_specification("PAXGUSDT")

    # Long fill at Open: Quote price + Half Spread + Slippage
    quote_open = 2700.0
    spread = 0.20  # half spread = 0.10
    fill_price, half_spread, slip, fee = engine.calculate_entry_fill(
        quote_price=quote_open,
        direction="LONG",
        quantity=100.0,
        spread=spread,
        spec=spec,
    )

    assert half_spread == 0.10
    assert slip == 0.15
    assert fill_price == 2700.0 + 0.10 + 0.15
    assert fee > 0


def test_matching_engine_short_fill():
    engine = MatchingEngine(fixed_slippage_usd=0.15)
    spec = get_instrument_specification("PAXGUSDT")

    # Short fill at Open: Quote price - Half Spread - Slippage
    quote_open = 2700.0
    spread = 0.20  # half spread = 0.10
    fill_price, half_spread, slip, fee = engine.calculate_entry_fill(
        quote_price=quote_open,
        direction="SHORT",
        quantity=100.0,
        spread=spread,
        spec=spec,
    )

    assert half_spread == 0.10
    assert slip == 0.15
    assert fill_price == 2700.0 - 0.10 - 0.15
    assert fee > 0
