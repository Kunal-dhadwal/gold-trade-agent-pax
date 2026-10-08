# REST & WEBSOCKET API SPECIFICATION
*FastAPI Endpoint Reference, Request/Response Schemas, and Real-Time Feeds*

---

## 1. REST API ENDPOINTS

Base URL: `http://localhost:8000/api/v1`

### 1.1 System & Health
- `GET /health`: Comprehensive health check (status of database, redis, disk space, and worker threads).
- `GET /system/info`: Platform metadata, active phase, instrument catalogue, version.

### 1.2 Market Data
- `POST /data/ingest`: Trigger ingestion for symbol (`XAUUSD` or `GC=F`) across timeframes.
- `GET /data/status`: Status of available historical datasets and date ranges.
- `GET /data/quality-reports`: List and view data quality validation audit reports.
- `GET /data/candles`: Query normalized OHLCV bars by symbol, timeframe, start/end dates.

### 1.3 Strategies
- `GET /strategies`: List all registered strategies and version tags.
- `GET /strategies/{strategy_id}`: Detailed specification, parameter definitions, and status.

### 1.4 Backtesting
- `POST /backtest/run`: Execute a deterministic backtest on selected dataset and parameters.
- `GET /backtest/{backtest_id}`: Retrieve backtest results, equity curve, and performance metrics.
- `GET /backtest/{backtest_id}/trades`: Retrieve individual trade log (entry, exit, PnL, MAE, MFE).

---

## 2. REAL-TIME WEBSOCKETS (PHASE 3)

- `WS /ws/market`: Live Gold ticker, best bid/ask, mark price, session, and detected regime.
- `WS /ws/paper`: Live simulated order updates, position state, unrealized P&L.
- `WS /ws/risk`: Real-time risk alerts and kill-switch circuit breaker status.
