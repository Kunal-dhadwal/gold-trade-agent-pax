"""Application configuration settings using Pydantic Settings."""

from pathlib import Path
from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    DEBUG: bool | str = True

    # API
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_PREFIX: str = "/api/v1"
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/gold_trading.db"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # Redis Cache (Phase 3 Component — Optional in Phase 1 & 2)
    REDIS_URL: Optional[str] = None

    # Data Storage Directories
    DATA_DIR: Path = Path("./data")
    RAW_DATA_DIR: Path = Path("./data/raw")
    NORMALIZED_DATA_DIR: Path = Path("./data/normalized")
    TIMEFRAME_DATA_DIR: Path = Path("./data/timeframe")
    QUALITY_REPORTS_DIR: Path = Path("./data/quality_reports")

    # Delta Exchange Configuration
    DELTA_API_BASE_URL: str = "https://api.delta.exchange"
    DELTA_TESTNET_API_BASE_URL: str = "https://testnet-api.delta.exchange"
    DELTA_WS_URL: str = "wss://socket.delta.exchange"
    DELTA_API_KEY: str = ""
    DELTA_API_SECRET: str = ""
    DELTA_TESTNET: bool = True

    # Safety Invariants
    LIVE_TRADING_ENABLED: bool = False
    PAPER_TRADING_ENABLED: bool = True

    # Risk Engine Default Parameters
    MAX_DAILY_LOSS_PCT: float = 2.0
    MAX_PORTFOLIO_DRAWDOWN_PCT: float = 6.0
    MAX_RISK_PER_TRADE_PCT: float = 1.0
    MAX_CONCURRENT_POSITIONS: int = 1
    MAX_CONSECUTIVE_LOSSES: int = 4
    MIN_RISK_REWARD_RATIO: float = 1.5


settings = Settings()
