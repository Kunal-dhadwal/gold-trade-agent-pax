# DATA PIPELINE & VALIDATION SPECIFICATION
*Ingestion, Sanitization, Multi-Timeframe Resampling, and Parquet Storage*

---

## 1. DATA SOURCES & INGESTION

The platform ingests Gold market data across free and public sources:

1. **Yahoo Finance (`GC=F`):**
   - Source for long-term continuous Gold Comex futures.
   - Resolutions: Daily (`1d`) spanning 20+ years, Hourly (`1h`) spanning 730 days, 5-minute (`5m`) spanning 60 days.
   - Used for macro trend analysis, long-term backtesting, and market regime baselines.
2. **Delta Exchange Public API (`https://api.delta.exchange/v2`):**
   - Source for executable Gold derivative instruments (`XAUUSD` / `GOLDUSD` perpetual and futures).
   - Endpoints:
     - `/v2/products`: Instrument metadata, tick size, contract specifications.
     - `/v2/history/candles`: Historical OHLCV candles across 1m, 5m, 15m, 30m, 1h, 4h, 1d.
     - `/v2/tickers`: Live order book best bid/ask, mark price, funding rates.
3. **Local Tick & Minute Parquet/CSV Archives:**
   - Ingestion of external open datasets (e.g., historical M1 or tick datasets).
   - Allows offline research and zero-dependency reproducibility.

---

## 2. FILE SYSTEM LAYOUT

All market datasets are stored deterministically under `data/`:

```
data/
├── raw/                      # Immutable raw payloads (JSON / CSV / Raw Parquet)
│   ├── yahoo/
│   │   └── GC=F/
│   │       └── raw_20240101_20241231.json
│   └── delta/
│       └── XAUUSD/
│           └── raw_candles_1m_2024.json
├── normalized/               # Canonical cleansed 1-minute or base candles
│   └── XAUUSD/
│       └── 1m/
│           ├── 2023.parquet
│           └── 2024.parquet
├── timeframe/                # Resampled higher timeframe data
│   └── XAUUSD/
│       ├── 5m.parquet
│       ├── 15m.parquet
│       ├── 30m.parquet
│       ├── 1h.parquet
│       ├── 4h.parquet
│       └── 1d.parquet
├── metadata/                 # Dataset manifests and SHA-256 checksums
│   └── dataset_registry.json
└── quality_reports/          # Automated data validation inspection audits
    ├── report_yahoo_GC_F_2024.json
    └── report_delta_XAUUSD_2024.json
```

---

## 3. CANONICAL NORMALIZED SCHEMA

Every normalized dataset must adhere strictly to the following PyArrow / Pandas schema:

| Column | Type | Unit / Format | Description |
|---|---|---|---|
| `timestamp` | `datetime64[ns, UTC]` | ISO-8601 UTC | Candle open timestamp |
| `open` | `float64` | USD | Opening price |
| `high` | `float64` | USD | Highest traded price during candle |
| `low` | `float64` | USD | Lowest traded price during candle |
| `close` | `float64` | USD | Closing traded price |
| `volume` | `float64` | Contracts / Oz | Total traded volume |
| `source` | `category / string` | `YAHOO`, `DELTA`, `LOCAL` | Origin data source |
| `instrument` | `category / string` | `XAUUSD`, `GC=F` | Canonical instrument symbol |

---

## 4. DATA QUALITY VALIDATION RULES

Before any dataset moves from `data/raw/` to `data/normalized/`, it passes through the `DataQualityEngine`:

1. **OHLC Structural Geometry:**
   - $High \ge \max(Open, Close)$
   - $Low \le \min(Open, Close)$
   - $High \ge Low > 0$
2. **Timestamp Monotonicity & Uniqueness:**
   - Strict ascending order: $t_{i} > t_{i-1}$
   - Zero duplicate timestamps allowed. Duplicate candles are identified and deduplicated (keeping latest revised bar or logging conflict).
3. **Volume Sanity:**
   - $Volume \ge 0$
4. **Price Spike / Outlier Detection:**
   - For every candle, compute price return $R_t = \frac{|Close_t - Open_t|}{ATR_{14, t-1}}$.
   - If $R_t > 8.0$ (an 8-ATR sudden candle without follow-through), flag as anomalous spike for manual or secondary validation.
5. **Gap Detection:**
   - Gaps exceeding normal market closure hours (e.g. weekend closures: Friday 21:00 UTC to Sunday 22:00 UTC for Gold futures) are classified as expected; weekday gaps $> 2 \times \text{timeframe}$ are flagged and logged in the data quality audit report.

---

## 5. MULTI-TIMEFRAME RESAMPLING

The system builds higher timeframes deterministically from validated 1-minute data:
$$\begin{aligned}
Open_{T} &= Open_{first} \\
High_{T} &= \max(High_{t \in T}) \\
Low_{T} &= \min(Low_{t \in T}) \\
Close_{T} &= Close_{last} \\
Volume_{T} &= \sum(Volume_{t \in T})
\end{aligned}$$
Resampled intervals: `5m`, `15m`, `30m`, `1h`, `4h`, `1d`. All timestamps align strictly to clean standard UTC boundaries (e.g. 00:00, 04:00, 08:00 for 4H).
