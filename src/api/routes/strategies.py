"""Strategy discovery and parameter inspection endpoints."""

from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException
from src.strategy.registry import StrategyRegistry
from src.api.schemas import StrategySummary

router = APIRouter(prefix="/strategies", tags=["Strategies"])


@router.get("", response_model=List[StrategySummary])
async def list_strategies():
    """List all registered quantitative strategies and specifications."""
    return StrategyRegistry.list_all()


@router.get("/{strategy_id}", response_model=StrategySummary)
async def get_strategy_details(strategy_id: str):
    """Retrieve parameter schemas and logic description for a specific strategy."""
    all_strats = StrategyRegistry.list_all()
    for s in all_strats:
        if s["strategy_id"] == strategy_id:
            return s
    raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found.")
