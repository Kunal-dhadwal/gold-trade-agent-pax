# AGentTrXPAUS Quantitative Strategy Dossier
## Strategy: XAU_TREND_FOLLOWING_1H_4

**Strategy ID:** `775f5a4e-b600-445f-95b2-74557364ae19`  
**Document Date:** 2026-10-12 12:51:07 UTC  
**Tournament Status:** **REJECTED** (DISQUALIFIED / REJECTED)  
**Tournament Rank:** Rank 2 (of 4 cumulative trials)  
**Composite Strategy Score (CSS):** **0.00 / 100.00**  
**Deflated Sharpe Ratio (DSR):** **0.00%**  

---

## 1. Executive Summary & Economic Hypothesis

- **Instrument:** `XAUUSD` (Spot Gold)
- **Primary Timeframe:** `1h`
- **Trade Direction:** `SHORT`
- **Target Regimes:** BREAKOUT
- **Data Source:** `DUKASCOPY`
- **Generation Lineage:** Generation 1, Trial #4
- **AST Cryptographic Fingerprint:** `2ba68afe9fd8bdfa...` (SHA-256)

### Economic Thesis
> Gold edge in BREAKOUT (1h): Structural liquidity and volume patterns identified by market structure analysis.

**Quantitative Rationale:**  
Systematic TREND_FOLLOWING hypothesis tailored for BREAKOUT regime.

---

## 2. Strategy Architecture & AST Specification

### A. Required Indicators
| Indicator Alias | Indicator Type | Configured Inputs |
| :--- | :--- | :--- |
| `ema_fast` | EMA | `period=16` |
| `ema_slow` | EMA | `period=36` |

### B. Logic & Signal Execution Rules
- **Entry Condition:** (`ema_fast[0] CROSS_UNDER ema_slow[0]`)
- **Exit Condition:** (`ema_fast[0] CROSS_OVER ema_slow[0]`)

### C. Protective Risk & Bracket Orders
- **Stop Loss Policy:** `ATR_MULTIPLE` (Multiplier/Value: `1.5`)
- **Take Profit Policy:** `ATR_MULTIPLE` (Multiplier/Value: `3.0`)
- **Max Holding Bars:** `48` bars
- **Allowed Trading Sessions:** ALL

---

## 3. In-Sample & Historical Backtest Performance

| Performance Metric | Result | Benchmark / Safety Policy | Gate Status |
| :--- | :--- | :--- | :---: |
| **Total Net Profit** | $-143.68 | > $0.00 | ❌ FAIL |
| **Total Return** | -1.44% | — | ℹ️ |
| **Gross Profit Factor** | 1.21 | Min 1.30 | ❌ FAIL |
| **Win Rate** | 37.50% | — | ℹ️ |
| **Total Trades** | 8 | Min 50 trades | ❌ FAIL |
| **Winning / Losing Trades** | 3 / 5 | — | ℹ️ |
| **Peak-to-Trough Drawdown** | 3.24% ($324.12) | Max 15.0% | ✅ PASS |
| **Drawdown Duration** | 317 bars | — | ℹ️ |
| **Annualized Sharpe Ratio** | -1.25 | — | ℹ️ |
| **Full-Sample Sortino Ratio** | -1.54 | — | ℹ️ |
| **Expectancy** | $-20.22 / trade | > 0.15 × spread | ❌ FAIL |
| **Average R-Multiple** | -0.19R | — | ℹ️ |
| **Max Consecutive Losses** | 2 | Max 8 | ✅ PASS |
| **Execution Fees Paid** | $262.59 | — | ℹ️ |
| **Execution Slippage Cost** | $17.96 | — | ℹ️ |

---

## 4. Walk-Forward Analysis (WFA) & Out-of-Sample Efficiency

> ⚠️ **[NOT_EVALUATED]** Walk-Forward Analysis was not executed. (Pipeline short-circuited at In-Sample hard gates).

---

## 5. Monte Carlo Robustness & Stress Testing

> ⚠️ **[NOT_EVALUATED]** Monte Carlo stress test was not executed. (Pipeline short-circuited at earlier gates).

---

## 6. Anti-Overfitting Controls & Multiple-Testing Audit (DSR)

### A. Deflated Sharpe Ratio (Bailey & López de Prado 2014)
- **Cumulative Strategy Trials Evaluated ($N$):** **4**
- **Expected Maximum Sharpe under Null ($E[\max_N]$):** **0.0000**
- **Observed Annualized Sharpe Ratio:** **-1.54**
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
- **Tournament Rank:** **#2**

### Decision & Rationale
> Rejected at validation pipeline: IN_SAMPLE_HARD_GATES: Trade count 8 < minimum gate 50.; Gross Profit Factor 1.21 < minimum 1.30.; Expectancy $-20.22 < spread threshold $0.04.

### Gate Rejection Checklist
- ❌ IN_SAMPLE_HARD_GATES: Trade count 8 < minimum gate 50.; Gross Profit Factor 1.21 < minimum 1.30.; Expectancy $-20.22 < spread threshold $0.04.

---

## 8. Production TradingView Pine Script v6

```pinescript
//@version=6
// AGentTrXPAUS Auto-Generated Trading Strategy: XAU_TREND_FOLLOWING_1H_4
// Strategy ID: 775f5a4e-b600-445f-95b2-74557364ae19
// Hypothesis: Gold edge in BREAKOUT (1h): Structural liquidity and volume patterns identified by market structure analysis.
// Target Instrument: XAUUSD | Timeframe: 1h
// Direction: SHORT | Regimes: ['BREAKOUT']
// Transpiled: 2026-10-12T12:51:07Z

strategy(title="XAU_TREND_FOLLOWING_1H_4", shorttitle="XAU_TREND_FOLLOW", overlay=true,
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

ema_fast = ta.ema(close, 16)

ema_slow = ta.ema(close, 36)


// ============================================================================
// Market Session Constraints (UTC Standard)
// ============================================================================
session_allowed = true

// ============================================================================
// Entry & Exit Conditions
// ============================================================================
entry_rule_triggered = ta.crossunder(ema_fast, ema_slow)
exit_rule_triggered = ta.crossover(ema_fast, ema_slow)

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
    strategy.entry("XAU_TREND_FOLLOWING_1H_4_Short", strategy.short)

if strategy.position_size < 0
    sl_price = strategy.position_avg_price + sl_distance
    tp_price = strategy.position_avg_price - tp_distance
    strategy.exit("XAU_TREND_FOLLOWING_1H_4_ExitShort", from_entry="XAU_TREND_FOLLOWING_1H_4_Short", stop=sl_price, limit=tp_price)

if exit_rule_triggered and strategy.position_size < 0
    strategy.close("XAU_TREND_FOLLOWING_1H_4_Short", comment="Exit Rule Triggered")

if p_max_holding_bars > 0 and strategy.position_size < 0
    in_trade = strategy.position_size < 0
    bars_in_trade = ta.barssince(in_trade and not in_trade[1])
    if bars_in_trade >= p_max_holding_bars
        strategy.close("XAU_TREND_FOLLOWING_1H_4_Short", comment="Max Holding Exceeded")

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
