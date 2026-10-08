# ADR-003: Redis Infrastructure Phase Boundary
**Status:** Approved & Frozen  
**Date:** 2026-10-08  
**Author:** Lead Quantitative Systems Engineer  
**Governing Documents:** `PROJECT_CONSTITUTION.md`, `ARCHITECTURE.md`, `docs/PHASE_1_SPEC.md`

---

## 1. CONTEXT & PROBLEM STATEMENT

The master project plan establishes a strict 4-phase sequential architecture:
- **Phase 1:** Foundation, Data Ingestion, Quantitative Features, Deterministic Backtester.
- **Phase 2:** AI Strategy Factory, Walk-Forward Analysis, Robustness Validation, Pine Generator.
- **Phase 3:** Paper Trading, Real-Time Risk Management, Live Dashboard, Telemetry.
- **Phase 4:** Live Delta Execution, Order Reconciliation, Production VPS Deployment.

During the Phase 1 implementation, Redis 7 was prematurely introduced into `docker-compose.yml`, and the backend service was configured with a blocking dependency:
```yaml
depends_on:
  redis:
    condition: service_healthy
```
Additionally, `fakeredis` was introduced into test dependencies.

However, forensic audit of the codebase revealed that **Phase 1 has zero runtime functional dependency on Redis**:
- Data ingestion writes directly to raw files and Parquet.
- Indicators and regimes compute deterministically in-memory via NumPy/Pandas.
- Backtesting runs synchronously in-process and writes results to PostgreSQL/SQLite.
- REST endpoints query the relational database directly.

Enforcing a Redis dependency in Phase 1 introduces premature operational complexity, blocks container startup if Redis is unavailable, and directly violates the frozen Phase 1 architectural boundary.

---

## 2. DECISION

1. **Redis is Strictly Frozen to Phase 3:**
   - Redis 7 is designated as a **Phase 3 infrastructure component**.
   - It will be activated in Phase 3 to handle:
     - Real-time tick and order book caching from Delta WebSocket feeds.
     - Pub/sub event bus for paper trading order matching.
     - Distributed state caching for the React/Vite dashboard.
2. **Phase 1 Has Zero Redis Dependency:**
   - The Phase 1 backend service must start and operate with complete functionality when Redis is completely absent.
   - `docker-compose.yml` must not gate backend startup on a Redis health condition during Phase 1.
   - Unit and integration tests for Phase 1 must not require a running Redis daemon or `fakeredis` mocks for core operations.
   - In `src/core/config.py`, Redis connection settings must be optional with graceful fallback or disabled flag during Phase 1 and Phase 2.

---

## 3. CONSEQUENCES

### Positive:
- **Zero-Friction Local Development:** Developers and CI/CD pipelines can run the entire Phase 1 test suite and local FastAPI server with nothing more than Python and local SQLite.
- **Clean Architectural Phase Integrity:** Enforces the principle that infrastructure is introduced only when its functional capability is actively required.
- **Reduced Surface Area for Failure:** Eliminates container startup race conditions and health check timeouts in Phase 1.

### Negative / Trade-offs:
- Asynchronous pub/sub event distribution across multiple processes is deferred until Phase 3 (in-process `asyncio` event bus is used in Phase 1).

---

## 4. COMPLIANCE & VERIFICATION

1. Automated tests must execute and pass in an environment with no Redis server running and `REDIS_URL` unset.
2. In `docker-compose.yml`, the backend service definition must not have `depends_on: redis: condition: service_healthy` blocking Phase 1 services.
3. The `/health` endpoint must report healthy status based on database and storage without failing if Redis is offline.

