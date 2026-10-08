# PINE SCRIPT GENERATOR SPECIFICATION
*Automated Transpilation of Validated Strategies into TradingView Pine Script v5*

---

## 1. OBJECTIVE

The Pine Script Generator converts validated Python strategy specifications (`BaseStrategy` instances and parameters) into clean, idiomatic TradingView Pine Script v5 scripts. This enables independent cross-verification of strategy signals on TradingView charts.

---

## 2. GENERATION WORKFLOW (PHASE 2)

```
Strategy Specification (Python Dict / Pydantic Model)
                   |
                   v
Template Engine (Jinja2 / Ast Transpiler)
                   |
                   v
Generated Pine Script v5 Code
                   |
                   v
Static Syntax & Structural Verification
                   |
                   v
Store in Strategy Version Record & Expose in API
```

---

## 3. PINE SCRIPT v5 COMPATIBILITY STANDARDS

1. Strictly `//@version=5`.
2. Indicator or Strategy declaration:
   ```pinescript
   //@version=5
   strategy("Gold AI - Strategy Name", overlay=true, initial_capital=10000, default_qty_type=strategy.percent_of_equity, default_qty_value=1)
   ```
3. Deterministic calculation: All calculations must use closed bar context (`[1]`) or explicit non-repainting flags (`barmerge.lookahead_off`) to prevent lookahead repainting.
4. Input parameters cleanly mapped to `input.int()`, `input.float()`, `input.bool()`.
5. Strategy alerts configured for external webhook integration.
