"""Core system constants, enums, and domain classifications."""

from enum import Enum


class InstrumentId(str, Enum):
    XAUUSD = "XAUUSD"
    GC_FUTURES = "GC=F"
    XAUUSD_PERP = "XAUUSD_PERP"
    PAXGUSDT = "PAXGUSDT"


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class DataSource(str, Enum):
    DUKASCOPY = "DUKASCOPY"
    YAHOO = "YAHOO"
    DELTA_EXCHANGE = "DELTA_EXCHANGE"
    LOCAL_ARCHIVE = "LOCAL_ARCHIVE"


class MarketSession(str, Enum):
    ASIAN = "ASIAN"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"
    LONDON_NY_OVERLAP = "LONDON_NY_OVERLAP"
    OFF_HOURS = "OFF_HOURS"


class OrderDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"


class ExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    SIGNAL_REVERSAL = "SIGNAL_REVERSAL"
    EXPIRY = "EXPIRY"
    RISK_HALT = "RISK_HALT"


class StrategyStatus(str, Enum):
    RESEARCH = "RESEARCH"
    BACKTESTED = "BACKTESTED"
    VALIDATION = "VALIDATION"
    OOS_PASSED = "OOS_PASSED"
    PAPER_TRADING = "PAPER_TRADING"
    LIVE_CANDIDATE = "LIVE_CANDIDATE"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


class MarketRegimeType(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    BREAKOUT = "BREAKOUT"
    REVERSAL = "REVERSAL"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    ABNORMAL = "ABNORMAL"
