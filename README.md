# GOLD AI TRADING AGENT
### Institutional-Grade AI Quantitative Research and Execution Platform for Gold (XAUUSD / Delta Exchange)

---

## PROJECT OVERVIEW

The **Gold AI Trading Agent** is a multi-agent quantitative research and trading system designed exclusively for **Gold (XAU/USD and Delta Exchange Gold derivatives)**. 

The architecture strictly adheres to a **4-Phase Sequenced Roadmap**:
- **Phase 1: Foundation + Data Pipeline + Deterministic Backtesting (CURRENT PHASE)**
- **Phase 2: AI Strategy Factory + Multi-Tier Validation Suite**
- **Phase 3: Real-Time Paper Trading + Deterministic Risk Engine + React Dashboard**
- **Phase 4: Delta Exchange Live Execution + VPS Deployment + Hardening**

---

## ARCHITECTURAL TENETS

1. **Deterministic Risk Precedence:** The Risk Engine has unconditional veto power over all AI, algorithmic, or manual orders.
2. **Deterministic Quant vs. AI Reasoning:** All mathematical indicators, bar-by-bar backtests, metrics, and position sizes are computed in high-performance Python/NumPy/Pandas. AI agents provide market reasoning, hypothesis generation, and Pine Script generation.
3. **Free-First Data Architecture:** Leverages Yahoo Finance (`GC=F`), Delta Exchange public market-data REST/WebSocket, and local open archives.
4. **Anti-Overfitting Discipline:** Rigorous partition of In-Sample, Validation, and Out-of-Sample datasets, combined with Walk-Forward Analysis and Monte Carlo simulations.
5. **Safety by Default:** Live trading is hard-disabled (`LIVE_TRADING_ENABLED=false`).

---

## QUICK START

### 1. Environment Setup
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run Tests
```bash
pytest -v
```

### 3. Launch Development Server
```bash
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Docker Deployment
```bash
docker compose up -d
```
