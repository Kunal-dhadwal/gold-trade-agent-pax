# DETERMINISTIC BACKTESTING SPECIFICATION
*Execution Realism, Slippage Modeling, Margin Simulation, and Metrics Calculations*

---

## 1. BACKTESTING DESIGN PRINCIPLES

The backtesting engine is strictly deterministic and bar-by-bar event-driven:
1. **Zero Lookahead Bias:**
   - Calculations for bar $t$ use only information available at or before bar $t$.
   - A signal triggered upon the close of bar $t$ executes at either bar $t$ close (with modeled execution delay & slippage) or bar $t+1$ open.
2. **Conservative Intra-Bar Fill Policy:**
   - If a candle's range encompasses both the Stop Loss price and the Take Profit price, the engine conservatively models that the **Stop Loss triggered first**, registering a loss.
3. **Execution Costs:**
   - Every transaction accounts for bid-ask spread, adverse market slippage, and taker/maker fee schedules.

---

## 2. MATHEMATICAL COST MODELS

### 2.1 Slippage Model
Execution price is adjusted adversely from the theoretical quote:
$$\text{Fill Price}_{\text{Buy}} = P_{\text{quote}} + \text{HalfSpread} + \text{Slip}(V_t, \sigma_t)$$
$$\text{Fill Price}_{\text{Sell}} = P_{\text{quote}} - \text{HalfSpread} - \text{Slip}(V_t, \sigma_t)$$
Where:
$$\text{Slip}(V_t, \sigma_t) = \text{BaseSlippage} + \kappa \cdot \text{ATR}_{14, t} \cdot \sqrt{\frac{\text{OrderSize}}{\text{MedianVolume}}}$$
For Gold / XAUUSD:
- Base spread assumption: \$0.20 - \$0.40 per ounce.
- Base slippage: \$0.10 - \$0.25 per ounce under normal liquidity.

### 2.2 Exchange Fee Model
Fees reflect Delta Exchange derivative fee schedule:
$$\text{Fee} = \text{Notional Value} \times \text{FeeRate}$$
- Maker Fee: 0.02% ($-0.0002$)
- Taker Fee: 0.05% ($0.0005$)

---

## 3. COMPREHENSIVE PERFORMANCE METRICS FORMULAS

1. **Cumulative & Annualized Return:**
   $$\text{Total Return} = \frac{E_{\text{final}} - E_0}{E_0}$$
   $$\text{CAGR} = \left(\frac{E_{\text{final}}}{E_0}\right)^{\frac{365.25}{\text{Days}}} - 1$$
2. **Sharpe Ratio (Annualized):**
   $$\text{Sharpe} = \frac{\mu_r - r_f}{\sigma_r} \times \sqrt{N_{\text{periods}}}$$
3. **Sortino Ratio (Institutional Downside Volatility):**
   $$\sigma_{\text{downside}} = \sqrt{\frac{1}{N} \sum_{i=1}^N \min(0, r_i - r_f)^2}$$
   $$\text{Sortino} = \frac{\mu_r - r_f}{\sigma_{\text{downside}}} \times \sqrt{N_{\text{periods}}}$$
   *(Note: Downside semi-deviation is calculated over all $N$ returns with non-negative returns clamped to zero, avoiding sample size bias).*
4. **Maximum Drawdown (MDD) & Duration:**
   $$\text{Drawdown}_t = \frac{\max_{\tau \le t} E_\tau - E_t}{\max_{\tau \le t} E_\tau}$$
   $$\text{MDD} = \max_t (\text{Drawdown}_t)$$
5. **Gross Profit Factor:**
   $$\text{Profit Factor} = \frac{\sum \text{Gross Gains}}{\sum |\text{Gross Losses}|}$$
   *(Note: Computed on gross trade returns before commission and funding fees).*
6. **Expectancy (USD & R-Multiple):**
   $$\text{Expectancy} = (\text{Win Rate} \times \text{Avg Win}) - (\text{Loss Rate} \times \text{Avg Loss})$$
   $$\text{Avg R} = \frac{1}{N}\sum_{i=1}^N \frac{\text{PnL}_i}{\text{Initial Risk}_i}$$
7. **Maximum Adverse Excursion (MAE) & Maximum Favorable Excursion (MFE):**
   $$\text{MAE}_i = \frac{\text{Worst Price During Trade} - \text{Entry Price}}{\text{Entry Price}}$$
   $$\text{MFE}_i = \frac{\text{Best Price During Trade} - \text{Entry Price}}{\text{Entry Price}}$$

