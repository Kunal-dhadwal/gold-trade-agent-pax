"""Paper Trading Module for Phase 3 AGentTrXPAUS."""

from src.paper.engine import EngineState, PaperTradingEngine
from src.paper.matching import MatchingEngine, OrderState, PaperOrder
from src.paper.parity import BacktestPaperParityTracker, ParityReport, ParityStatus
from src.paper.portfolio import SingleStrategyPortfolio
from src.paper.positions import PositionTracker
from src.paper.promotion import PaperPromotionEvaluator, PromotionEvaluationReport
from src.paper.specifications import InstrumentSpecification, get_instrument_specification

__all__ = [
    "PaperTradingEngine",
    "EngineState",
    "MatchingEngine",
    "PaperOrder",
    "OrderState",
    "BacktestPaperParityTracker",
    "ParityReport",
    "ParityStatus",
    "SingleStrategyPortfolio",
    "PositionTracker",
    "PaperPromotionEvaluator",
    "PromotionEvaluationReport",
    "InstrumentSpecification",
    "get_instrument_specification",
]

