# DATA PIPELINE & VALIDATION SPECIFICATION
*Ingestion, Sanitization, Multi-Timeframe Resampling, and Parquet Storage*

---

## 1. DATA SOURCES & INGESTION PRIORITY

The platform ingests Gold market data across free and public sources, maintaining strict instrument and provenance separation:

1. **Dukascopy (`XAUUSD`) — PRIMARY RESEARCH DATASET:**
   - Institutional spot gold data (1 troy ounce quoted in USD).
   - Continuous historical tick and 1-minute archives spanning multiple years without futures contract roll gaps.
   - Primary baseline for quantitative indicator computation, regime detection, and strategy backtesting.
2. **Yahoo Finance (`GC=F`) — SECONDARY RESEARCH DATASET:**
   - Continuous COMEX Gold futures contract (100 troy ounces per contract).
   - Used for macro trend analysis, multi-decade historical context, and futures comparison.
   - Must never silently replace Dukascopy `XAUUSD` as the primary baseline.
3. **Delta Exchange Public API (`https://api.delta.exchange/v2`) — EXECUTION VENUE DATA:**
   - Source for executable Gold derivative instruments (`PAXGUSDT` / `XAUUSD_PERP`).
   - Endpoints:
     - `/v2/products`: Instrument metadata, tick size, contract multiplier, fee rates, funding rules.
     - `/v2/history/candles`: Public historical OHLCV candles across 1m, 5m, 15m, 30m, 1h, 4h, 1d.
     - `/v2/tickers`: Live order book best bid/ask, mark price, funding rates.
4. **Local Tick & Minute Parquet/CSV Archives:**
   - Ingestion of external open datasets (e.g., historical M1 or tick datasets).
   - Allows offline research and zero-dependency reproducibility.

---

## 2. FILE SYSTEM LAYOUT

All market datasets are stored deterministically under `data/`:

```text
data/
├── raw/                      # Immutable raw payloads (JSON / CSV / Raw Parquet)
│   ├── dukascopy/
│   │   └── XAUUSD/
│   │       └── raw_20240101_20241231.parquet
│   ├── yahoo/
│   │   └── GC=F/
│   │       └── raw_20240101_20241231.json
│   └── delta/
│       └── PAXGUSDT/
│           └── raw_candles_1m_2024.json
├── normalized/               # Canonical cleansed base candles
│   ├── dukascopy/
│   │   └── XAUUSD/
│   │       └── 1m/
│   │           ├── 2023.parquet
│   │           └── 2024.parquet
│   └── yahoo/
│       └── GC=F/
│           └── 1d/
│               └── continuous.parquet
├── timeframe/                # Resampled higher timeframe data
│   └── dukascopy/
│       └── XAUUSD/
│           ├── 5m.parquet
│           ├── 15m.parquet
│           ├── 30m.parquet
│           ├── 1h.parquet
│           ├── 4h.parquet
│           └── 1d.parquet
├── metadata/                 # Dataset manifests and SHA-256 checksums
│   └── dataset_registry.json
└── quality_reports/          # Automated data validation inspection audits
    ├── report_dukascopy_XAUUSD_2024.json
    ├── report_yahoo_GC_F_2024.json
    └── report_delta_PAXGUSDT_2024.json
```

---

## 3. CANONICAL NORMALIZED SCHEMA

Every normalized dataset must adhere strictly to the following PyArrow / Pandas schema:

| Column | Type | Unit / Format | Description |
|---|---|---|---|
| `timestamp` | `datetime64[ns, UTC]` | ISO-8601 UTC | Candle open timestamp (strictly monotonic, unique) |
| `open` | `float64` | USD / USDT | Opening price ($>0$, non-null, finite) |
| `high` | `float64` | USD / USDT | Highest traded price during candle |
| `low` | `float64` | USD / USDT | Lowest traded price during candle |
| `close` | `float64` | USD / USDT | Closing traded price ($>0$, non-null, finite) |
| `volume` | `float64` | Contracts / Oz | Total traded volume ($\ge 0$, non-null, finite) |
| `source` | `category / string` | `DUKASCOPY`, `YAHOO`, `DELTA`, `LOCAL` | Origin data source |
| `instrument` | `category / string` | `XAUUSD`, `GC=F`, `PAXGUSDT` | Canonical instrument symbol |

---

## 4. DATA QUALITY VALIDATION RULES

Before any dataset moves from `data/raw/` to `data/normalized/`, it passes through the `DataQualityEngine`:

1. **NaN / Null Quarantine:**
   - Any candle containing `NaN`, `None`, or `inf` values in `open`, `high`, `low`, `close`, or `volume` is quarantined and excluded from `clean_df`.
2. **OHLC Structural Geometry:**
   - $High \ge \max(Open, Close)$
   - $Low \le \min(Open, Close)$
   - $High \ge Low > 0$
   - $Open > 0$ and $Close > 0$
3. **Timestamp Monotonicity & Uniqueness:**
   - Strict ascending order: $t_{i} > t_{i-1}$
   - Zero duplicate timestamps allowed. Duplicate candles are identified and deduplicated (keeping latest revised bar or logging conflict).
4. **Volume Sanity:**
   - $Volume \ge 0$
5. **Price Spike / Outlier Detection:**
   - For every candle, compute price jump relative to trailing ATR: $\frac{|Close_t - Close_{t-1}|}{ATR_{20, t-1}}$.
   - If jump $> 5.0\sigma$, flag as anomalous spike in quality report.
6. **Session Gap Detection:**
   - Missing candle intervals are detected relative to Gold trading market sessions (Sunday 22:00 UTC to Friday 21:00 UTC).
7. **Quality Scoring & Reporting:**
   - Produces composite quality score ($0.0 - 100.0$) and writes structured JSON and Markdown audit reports to `data/quality_reports/`.

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

Resampled intervals: `5m`, `15m`, `30m`, `1h`, `4h`, `1d`. All timestamps align strictly to left-closed standard UTC boundaries (e.g. 00:00, 04:00, 08:00 for 4H) with zero lookahead bias.
