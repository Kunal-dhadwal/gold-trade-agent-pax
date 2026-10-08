# DELTA EXCHANGE INTEGRATION ARCHITECTURE
*Exchange Adapter Interfaces, Rate Limiting, REST/WebSocket Specs, and Order Reconciliation*

---

## 1. INTEGRATION ARCHITECTURE

The platform interacts with Delta Exchange through an isolated adapter layer (`src/execution/delta/`):

```
+-------------------------------------------------------------+
|                      Trading Engine                         |
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
|               ExchangeAdapter (Abstract Interface)         |
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
|             DeltaExchangeAdapter (Concrete Implementation)  |
|  - REST Client (Products, Order Book, Candles, Orders)      |
|  - WebSocket Client (L2 Book, Tickers, User Fills)          |
|  - Rate Limiter & HMAC SHA-256 Request Signer               |
|  - Order & Position Reconciliation Daemon                   |
+-------------------------------------------------------------+
```

---

## 2. API ENDPOINTS & PROTOCOLS

- **Base URL (Production):** `https://api.delta.exchange`
- **Base URL (Testnet):** `https://testnet-api.delta.exchange`
- **WebSocket URL:** `wss://socket.delta.exchange`

### Key REST Endpoints:
1. `GET /v2/products`: Contract specifications, tick sizes, strike prices, expiry dates.
2. `GET /v2/history/candles`: Historical OHLCV candle feed.
3. `GET /v2/tickers`: Real-time mark price, 24h volume, funding rates, open interest.
4. `POST /v2/orders` (Auth): Submit limit/market order with client order ID.
5. `GET /v2/orders` (Auth): Retrieve open orders.
6. `DELETE /v2/orders` (Auth): Cancel order.
7. `GET /v2/positions` (Auth): Active contract positions and liquidation thresholds.

---

## 3. ORDER & POSITION RECONCILIATION

In Phase 4, the Reconciliation Daemon executes every 10 seconds:
1. Query exchange positions and compare with internal tracking state.
2. If discrepancy $> 0.0001$ contracts, immediately trigger `RECONCILIATION_MISMATCH_ALERT` and freeze new order submissions.
3. Verify all open positions have active Stop Loss orders registered on the exchange or in the local monitor.
