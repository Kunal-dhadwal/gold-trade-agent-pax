# DEPLOYMENT & INFRASTRUCTURE SPECIFICATION
*Docker Compose Multi-Container Setup, Local Execution, and VPS Deployment*

---

## 1. INFRASTRUCTURE COMPONENTS

The production deployment consists of decoupled Docker containers orchestrated via `docker-compose.yml`:
1. **`db` (PostgreSQL 16):** Transactional state, strategy repository, metrics, and audit tables.
2. **`cache` (Redis 7):** Real-time price cache, rate limiting, and pub/sub channels.
3. **`backend` (FastAPI / Uvicorn):** Quantitative research engine, data pipeline, and REST/WebSocket API.
4. **`frontend` (Vite / React / Nginx):** Institutional trading dashboard.

---

## 2. ENVIRONMENT PROFILES

- **`development` (Local Zero-Config):**
  - Database: Local PostgreSQL or SQLite fallback (`sqlite+aiosqlite`) for instant developer productivity.
  - Cache: Redis or `fakeredis` fallback.
  - Execution: Paper trading or historical backtest mode.
- **`production` (Docker Compose / Linux VPS):**
  - PostgreSQL container with persistent volume mount (`pg_data`).
  - Redis container with append-only file persistence (`redis_data`).
  - FastAPI container managed under Uvicorn with auto-restart policies.
  - Health checks on all containers.
