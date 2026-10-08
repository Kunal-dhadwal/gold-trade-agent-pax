# DETERMINISTIC RISK ENGINE & EMERGENCY KILL SWITCH
*Rules-Based Risk Evaluation, Position Sizing, and Automated Circuit Breakers*

---

## 1. CORE DESIGN PRINCIPLES

1. **Absolute Precedence:** The Risk Engine has unconditional veto power over all strategy signals, AI recommendations, and manual orders.
2. **Deterministic Evaluation:** Written in pure, testable Python code with zero external LLM dependencies.
3. **Fail-Closed Architecture:** If any check encounters an ambiguous state, unexpected exception, or missing market quote, the decision defaults to **REJECT ORDER / HALT TRADING**.

---

## 2. DETERMINISTIC RISK RULES

Every prospective trade must pass all 8 risk gates:

| Gate | Check Name | Default Threshold | Action on Breach |
|---|---|---|---|
| 1 | `MAX_DAILY_LOSS` | $\le 2.0\%$ of starting daily equity | Veto signal, halt further trades until next day UTC 00:00 |
| 2 | `MAX_PORTFOLIO_DRAWDOWN` | $\le 6.0\%$ peak-to-trough | Engage Emergency Kill Switch, cancel orders, flatten positions |
| 3 | `MAX_CONSECUTIVE_LOSSES` | $\le 4$ consecutive losing trades | Cooldown period (24 hours) for the offending strategy |
| 4 | `MAX_RISK_PER_TRADE` | $\le 1.0\%$ of portfolio equity | Resize order downward or reject if below minimum contract size |
| 5 | `MAX_OPEN_POSITIONS` | $\le 1$ concurrent Gold position | Reject new signal |
| 6 | `MIN_RISK_REWARD` | Reward-to-Risk $\ge 1.5$ | Reject signal with substandard RR |
| 7 | `MANDATORY_STOP_LOSS` | Explicit SL required at order submission | Immediate rejection if SL is absent or invalid |
| 8 | `SPREAD_AND_VOLATILITY` | Spread $\le 3\times$ median spread | Reject entry during illiquid spread blowouts |

---

## 3. POSITION SIZING ENGINE

Position sizing calculates units based on exact account risk:
$$\text{Dollar Risk} = \text{Current Equity} \times \text{RiskPercentage}$$
$$\text{Risk Per Unit} = |\text{Entry Price} - \text{Stop Loss Price}|$$
$$\text{Calculated Size} = \frac{\text{Dollar Risk}}{\text{Risk Per Unit} \times \text{Contract Multiplier}}$$
Constraints:
$$\text{Final Size} = \text{Clamp}(\text{Calculated Size}, \text{MinLotSize}, \text{MaxAllowedPosition})$$

---

## 4. EMERGENCY KILL SWITCH (CIRCUIT BREAKER)

The Kill Switch acts as an automated emergency circuit breaker:
- **Triggers:**
  - Max portfolio drawdown breached.
  - Data feed heartbeat lost ($> 3$ missed keepalive intervals).
  - Exchange API returns repeated 5xx or authentication failures.
  - Position reconciliation discrepancy detected between local state and exchange balance.
- **Actions:**
  1. Set system state to `EMERGENCY_HALT`.
  2. Cancel all pending unfilled orders on exchange / paper engine.
  3. Emit high-priority `RISK_EVENT` alert to database and WebSocket stream.
  4. Optionally market-close open positions (configurable by operator).
  5. Require manual administrative reset to re-arm.
