"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from src.core.config import settings
from src.core.logger import logger
from src.database.connection import init_db
from src.api.routes import health, data, strategies, backtest, research


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize database and verify startup state."""
    logger.info("Initializing Gold AI Quant Application (Phase 1)...")
    await init_db()
    yield
    logger.info("Application shutting down.")


app = FastAPI(
    title="Gold AI Quant Research & Trading Platform",
    description="Institutional-grade quantitative research and execution engine for Gold (XAUUSD / Delta Exchange).",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception during {request.method} {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error occurred. Check system logs for details."},
    )


# Include API routers
app.include_router(health.router, prefix=settings.API_PREFIX)
app.include_router(data.router, prefix=settings.API_PREFIX)
app.include_router(strategies.router, prefix=settings.API_PREFIX)
app.include_router(backtest.router, prefix=settings.API_PREFIX)
app.include_router(research.router, prefix=settings.API_PREFIX)


@app.get("/")
async def root():
    return {
        "system": "Gold AI Trading Agent",
        "phase": "Phase 1: Foundation + Data + Backtesting",
        "docs_url": "/docs",
        "health_url": f"{settings.API_PREFIX}/health",
    }
