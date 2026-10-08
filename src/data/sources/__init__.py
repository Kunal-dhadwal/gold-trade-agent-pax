"""Data source connectors for Gold market data."""

from src.data.sources.delta import DeltaExchangeClient
from src.data.sources.dukascopy import DukascopyClient
from src.data.sources.local import LocalFileClient
from src.data.sources.yahoo import YahooFinanceClient

__all__ = [
    "DukascopyClient",
    "YahooFinanceClient",
    "DeltaExchangeClient",
    "LocalFileClient",
]

