"""Immutable instrument specifications governing paper trading and risk limits."""

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class InstrumentSpecification:
    """Immutable contract specification for execution instruments."""

    symbol: str
    contract_size: float  # Multiplier in troy ounces per contract (e.g. 0.001 oz for PAXGUSDT)
    quantity_step: float  # Minimum quantity increment
    minimum_quantity: float  # Minimum allowable order size
    maximum_quantity: float  # Maximum allowable order size
    tick_size: float  # Minimum price movement
    tick_value: float  # Dollar value of 1 tick = tick_size * contract_size
    margin_currency: str  # Settlement currency (e.g. USDT)
    taker_fee_rate: float  # Taker fee rate (e.g. 0.0005 = 0.05%)
    maker_fee_rate: float  # Maker fee rate (e.g. 0.0002 = 0.02%)

    def calculate_notional(self, price: float, quantity: float) -> float:
        """Calculate total notional value: Price * Quantity * ContractSize."""
        return price * quantity * self.contract_size

    def calculate_fee(self, price: float, quantity: float, is_maker: bool = False) -> float:
        """Calculate exchange fee for order."""
        notional = self.calculate_notional(price, quantity)
        rate = self.maker_fee_rate if is_maker else self.taker_fee_rate
        return notional * rate

    def round_quantity(self, quantity: float) -> float:
        """Round quantity down to nearest valid step."""
        if quantity <= 0:
            return 0.0
        steps = int(quantity / self.quantity_step)
        return round(steps * self.quantity_step, 6)

    def validate_quantity(self, quantity: float) -> bool:
        """Validate whether quantity respects minimum and maximum limits."""
        if quantity < self.minimum_quantity or quantity > self.maximum_quantity:
            return False
        # Check alignment with quantity_step
        remainder = (quantity * 1e6) % (self.quantity_step * 1e6)
        return abs(remainder) < 1e-4 or abs(remainder - self.quantity_step * 1e6) < 1e-4


_SPECIFICATIONS: Dict[str, InstrumentSpecification] = {
    "PAXGUSDT": InstrumentSpecification(
        symbol="PAXGUSDT",
        contract_size=0.001,
        quantity_step=0.01,
        minimum_quantity=0.01,
        maximum_quantity=10000.0,
        tick_size=0.01,
        tick_value=0.00001,
        margin_currency="USDT",
        taker_fee_rate=0.0005,
        maker_fee_rate=0.0002,
    ),
    "XAUUSD_PERP": InstrumentSpecification(
        symbol="XAUUSD_PERP",
        contract_size=0.001,
        quantity_step=0.01,
        minimum_quantity=0.01,
        maximum_quantity=10000.0,
        tick_size=0.01,
        tick_value=0.00001,
        margin_currency="USDT",
        taker_fee_rate=0.0005,
        maker_fee_rate=0.0002,
    ),
    "XAUUSD": InstrumentSpecification(
        symbol="XAUUSD",
        contract_size=1.0,
        quantity_step=0.01,
        minimum_quantity=0.01,
        maximum_quantity=100.0,
        tick_size=0.01,
        tick_value=0.01,
        margin_currency="USD",
        taker_fee_rate=0.0002,
        maker_fee_rate=0.0001,
    ),
}


def get_instrument_specification(symbol: str) -> InstrumentSpecification:
    """Retrieve immutable specification for a symbol or fail closed."""
    cleaned = symbol.strip().upper()
    if cleaned not in _SPECIFICATIONS:
        raise ValueError(
            f"Unsupported or unverified execution instrument: '{symbol}'. "
            f"Available specifications: {list(_SPECIFICATIONS.keys())}. Fail-closed enforced."
        )
    return _SPECIFICATIONS[cleaned]

