"""Unit tests for InstrumentSpecification and contract math."""

import pytest
from src.paper.specifications import get_instrument_specification, InstrumentSpecification


def test_paxgusdt_contract_specification():
    spec = get_instrument_specification("PAXGUSDT")
    assert spec.symbol == "PAXGUSDT"
    assert spec.contract_size == 0.001  # 1000 contracts = 1 troy oz
    assert spec.tick_size == 0.01
    assert spec.quantity_step == 0.01
    assert spec.minimum_quantity == 0.01


def test_notional_calculation():
    spec = get_instrument_specification("PAXGUSDT")
    # Price $2700, 1000 contracts -> 1 oz -> $2700
    notional = spec.calculate_notional(price=2700.0, quantity=1000.0)
    assert notional == 2700.0

    # 10 contracts -> 0.01 oz -> $27.00
    notional_small = spec.calculate_notional(price=2700.0, quantity=10.0)
    assert notional_small == 27.0


def test_fee_calculation():
    spec = get_instrument_specification("PAXGUSDT")
    # Notional $2700, taker fee rate 0.0005 -> $1.35
    fee = spec.calculate_fee(price=2700.0, quantity=1000.0, is_maker=False)
    assert fee == pytest.approx(1.35, rel=1e-4)

    # Maker fee rate 0.0002 -> $0.54
    maker_fee = spec.calculate_fee(price=2700.0, quantity=1000.0, is_maker=True)
    assert maker_fee == pytest.approx(0.54, rel=1e-4)


def test_quantity_rounding_and_validation():
    spec = get_instrument_specification("PAXGUSDT")
    assert spec.round_quantity(12.756) == 12.75
    assert spec.round_quantity(0.005) == 0.0

    assert spec.validate_quantity(10.0) is True
    assert spec.validate_quantity(0.005) is False
    assert spec.validate_quantity(200000.0) is False

