# AGentTrXPAUS Quantitative Strategy Dossier
## Strategy: XAU_TREND_FOLLOWING_1H_1

**Strategy ID:** `ffdbf5eb-428e-453a-b0ce-01a31503e8e1`  
**Document Date:** 2026-10-12 12:55:41 UTC  
**Tournament Status:** **REJECTED** (DISQUALIFIED / REJECTED)  
**Tournament Rank:** Rank 1 (of 1 cumulative trials)  
**Composite Strategy Score (CSS):** **0.00 / 100.00**  
**Deflated Sharpe Ratio (DSR):** **0.00%**  

---

## 1. Executive Summary & Economic Hypothesis

- **Instrument:** `XAUUSD` (Spot Gold)
- **Primary Timeframe:** `1h`
- **Trade Direction:** `LONG`
- **Target Regimes:** TRENDING_BULL
- **Data Source:** `DUKASCOPY`
- **Generation Lineage:** Generation 1, Trial #1
- **AST Cryptographic Fingerprint:** `60356f765cbec58f...` (SHA-256)

### Economic Thesis
> Gold edge in TRENDING_BULL (1h): Structural liquidity and volume patterns identified by market structure analysis.

**Quantitative Rationale:**  
Systematic TREND_FOLLOWING hypothesis tailored for TRENDING_BULL regime.

---

## 2. Strategy Architecture & AST Specification

### A. Required Indicators
| Indicator Alias | Indicator Type | Configured Inputs |
| :--- | :--- | :--- |
| `ema_fast` | EMA | `period=10` |
| `ema_slow` | EMA | `period=24` |

### B. Logic & Signal Execution Rules
- **Entry Condition:** (`ema_fast[0] CROSS_OVER ema_slow[0]`)
- **Exit Condition:** (`ema_fast[0] CROSS_UNDER ema_slow[0]`)

### C. Protective Risk & Bracket Orders
- **Stop Loss Policy:** `ATR_MULTIPLE` (Multiplier/Value: `1.5`)
- **Take Profit Policy:** `ATR_MULTIPLE` (Multiplier/Value: `3.0`)
- **Max Holding Bars:** `48` bars
- **Allowed Trading Sessions:** ALL

---

## 3. In-Sample & Historical Backtest Performance

| Performance Metric | Result | Benchmark / Safety Policy | Gate Status |
| :--- | :--- | :--- | :---: |
| **Total Net Profit** | $1,811.11 | > $0.00 | ✅ PASS |
| **Total Return** | 18.11% | — | ℹ️ |
| **Gross Profit Factor** | 99.00 | Min 1.30 | ✅ PASS |
| **Win Rate** | 100.00% | — | ℹ️ |
| **Total Trades** | 11 | Min 50 trades | ❌ FAIL |
| **Winning / Losing Trades** | 11 / 0 | — | ℹ️ |
| **Peak-to-Trough Drawdown** | 0.11% ($12.10) | Max 15.0% | ✅ PASS |
| **Drawdown Duration** | 792 bars | — | ℹ️ |
| **Annualized Sharpe Ratio** | 26.54 | — | ℹ️ |
| **Full-Sample Sortino Ratio** | 53.74 | — | ℹ️ |
| **Expectancy** | $164.65 / trade | > 0.15 × spread | ✅ PASS |
| **Average R-Multiple** | 1.52R | — | ℹ️ |
| **Max Consecutive Losses** | 0 | Max 8 | ✅ PASS |
| **Execution Fees Paid** | $432.24 | — | ℹ️ |
| **Execution Slippage Cost** | $64.30 | — | ℹ️ |

---

## 4. Walk-Forward Analysis (WFA) & Out-of-Sample Efficiency

> ⚠️ **[NOT_EVALUATED]** Walk-Forward Analysis was not executed. (Pipeline short-circuited at In-Sample hard gates).

---

## 5. Monte Carlo Robustness & Stress Testing

> ⚠️ **[NOT_EVALUATED]** Monte Carlo stress test was not executed. (Pipeline short-circuited at earlier gates).

---

## 6. Anti-Overfitting Controls & Multiple-Testing Audit (DSR)

### A. Deflated Sharpe Ratio (Bailey & López de Prado 2014)
- **Cumulative Strategy Trials Evaluated ($N$):** **1**
- **Expected Maximum Sharpe under Null ($E[\max_N]$):** **0.0000**
- **Observed Annualized Sharpe Ratio:** **53.74**
- **Deflated Sharpe Ratio (DSR):** **0.00%**
- **Significance Threshold:** `95.0%` ($\alpha = 0.05$)
- **DSR Gate Status:** ❌ **FAILED** (Unreliable edge under multiple testing)

### B. Quarantined 20% Out-of-Sample Holdout Gate
> **Architectural Security Assertion:** The final 20% holdout partition was physically air-gapped 
> during ideation, feature summarization, parameter selection, and Walk-Forward Analysis.

> ℹ️ **[NOT_EVALUATED]** Holdout partition was not evaluated (strategy was disqualified prior to terminal gate, or holdout was not requested).

---

## 7. Composite Strategy Score (CSS) & Tournament Ranking

- **Final Composite Strategy Score (CSS):** **0.00 / 100.00**
- **Quantitative Tier:** **REJECTED**
- **Tournament Rank:** **#1**

### Decision & Rationale
> Rejected at validation pipeline: IN_SAMPLE_HARD_GATES: Trade count 11 < minimum gate 50.

### Gate Rejection Checklist
- ❌ IN_SAMPLE_HARD_GATES: Trade count 11 < minimum gate 50.

---

## 8. Production TradingView Pine Script v6

```pinescript
//@version=6
// AGentTrXPAUS Auto-Generated Trading Strategy: XAU_TREND_FOLLOWING_1H_1
// Strategy ID: ffdbf5eb-428e-453a-b0ce-01a31503e8e1
// Hypothesis: Gold edge in TRENDING_BULL (1h): Structural liquidity and volume patterns identified by market structure analysis.
// Target Instrument: XAUUSD | Timeframe: 1h
// Direction: LONG | Regimes: ['TRENDING_BULL']
// Transpiled: 2026-10-12T12:55:38Z

strategy(title="XAU_TREND_FOLLOWING_1H_1", shorttitle="XAU_TREND_FOLLOW", overlay=true,
         initial_capital=10000.0,
         default_qty_type=strategy.percent_of_equity, default_qty_value=1.0,
         commission_type=strategy.commission.percent, commission_value=0.0500,
         slippage=1, pyramiding=0,
         process_orders_on_close=true,
         calc_on_every_tick=false,
         calc_on_order_fills=false)

// ============================================================================
// Strategy Inputs & Tunable Parameters
// ============================================================================
// (No optimizable parameters declared in AST)

// Risk & Position Management Inputs
p_sl_multiplier = input.float(1.50, title="Stop Loss Multiplier (ATR_MULTIPLE)", minval=0.1, step=0.1, group="Risk Management")
p_tp_multiplier = input.float(3.00, title="Take Profit Multiplier (ATR_MULTIPLE)", minval=0.1, step=0.1, group="Risk Management")
p_max_holding_bars = input.int(48, title="Max Holding Bars", minval=1, maxval=500, group="Risk Management")

// ============================================================================
// Technical Indicators (Strict Non-Repainting Calculation)
// ============================================================================
// Baseline ATR for volatility-adjusted stop-loss & take-profit
atr_baseline = ta.atr(14)

ema_fast = ta.ema(close, 10)

ema_slow = ta.ema(close, 24)


// ============================================================================
// Market Session Constraints (UTC Standard)
// ============================================================================
session_allowed = true

// ============================================================================
// Entry & Exit Conditions
// ============================================================================
entry_rule_triggered = ta.crossover(ema_fast, ema_slow)
exit_rule_triggered = ta.crossunder(ema_fast, ema_slow)

can_enter = entry_rule_triggered and session_allowed

// ============================================================================
// Stop-Loss & Take-Profit Calculation
// ============================================================================
sl_distance = math.max(atr_baseline * p_sl_multiplier, 0.50)
tp_distance = math.max(atr_baseline * p_tp_multiplier, 0.75)

// ============================================================================
// Order Execution & Trade Lifecycle Management
// ============================================================================
if can_enter and strategy.position_size == 0
    strategy.entry("XAU_TREND_FOLLOWING_1H_1_Long", strategy.long)

if strategy.position_size > 0
    sl_price = strategy.position_avg_price - sl_distance
    tp_price = strategy.position_avg_price + tp_distance
    strategy.exit("XAU_TREND_FOLLOWING_1H_1_ExitLong", from_entry="XAU_TREND_FOLLOWING_1H_1_Long", stop=sl_price, limit=tp_price)

if exit_rule_triggered and strategy.position_size > 0
    strategy.close("XAU_TREND_FOLLOWING_1H_1_Long", comment="Exit Rule Triggered")

if p_max_holding_bars > 0 and strategy.position_size > 0
    in_trade = strategy.position_size > 0
    bars_in_trade = ta.barssince(in_trade and not in_trade[1])
    if bars_in_trade >= p_max_holding_bars
        strategy.close("XAU_TREND_FOLLOWING_1H_1_Long", comment="Max Holding Exceeded")

// ============================================================================
// Visual Indicators & Execution Diagnostics
// ============================================================================
plot(ema_fast, title="EMA_FAST", color=color.blue, linewidth=2)
plot(ema_slow, title="EMA_SLOW", color=color.blue, linewidth=2)
```

---

## 9. Data Provenance & Methodological Limitations

1. **Data Provenance:** Historical XAUUSD 1-hour tick/bar data sourced from Dukascopy / Yahoo Finance.
2. **Execution Friction:** Simulations deduct $0.30/oz half-spread and 2 ticks of slippage.
3. **Non-Repainting Invariants:** Generated Pine Script enforces `process_orders_on_close = true`, `calc_on_every_tick = false`, and `calc_on_order_fills = false`.
4. **Compilation Boundary:** Pine Script syntax is validated offline via AST static parsing (`PineStaticValidator`). Official cloud compilation occurs on TradingView.
5. **Live Trading Authority:** `LIVE_TRADING_ENABLED = False` hard-locked. Live execution is an optional downstream capability governed by frozen safety invariants.
