# PAPER TRADING SPECIFICATION
*Real-Time Simulation, Execution Realism, Trade Journaling, and State Tracking*

---

## 1. PURPOSE & BOUNDARIES

Paper trading runs identical code paths to live execution, processing real-time market data without transmitting live orders to the exchange.

```
Real-Time Market Data
       |
       v
Strategy Engine (Signal)
       |
       v
Deterministic Risk Engine (Veto / Sizing)
       |
       v
Paper Trading Simulator (Fill & Slippage)
       |
       +--> Trade Journal (DB & State)
       +--> Real-Time WebSocket (Dashboard)
```

---

## 2. REALISTIC PAPER EXECUTION SIMULATION

1. **Order Types Supported:**
   - `MARKET`: Filled at best current bid/ask with volatility-adjusted slippage.
   - `LIMIT`: Placed into virtual order book; filled only when subsequent trades touch or cross the limit price.
2. **Stop Loss & Take Profit Management:**
   - Tracked bar-by-bar or tick-by-tick.
   - Server-side bracket monitoring guarantees stops are honored without relying on external stop orders.
3. **Trade Journaling:**
   - Every paper order records: Signal ID, Entry Time, Exit Time, Entry Price, Exit Price, Size, Gross P&L, Net P&L, Slippage Cost, Fees Paid, MFE, MAE, R-Multiple, and Regime Context.
