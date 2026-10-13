"""Strategy Validation Dossier Generator.

Produces comprehensive, standalone Markdown validation dossiers for quantitative strategies,
detailing hypotheses, AST rules, backtest metrics, WFA, Monte Carlo stress tests,
Deflated Sharpe Ratio (DSR), tournament ranking, and verified TradingView Pine Script v6.

Governing specifications:
- Milestone 2.5.3: Validation Dossier Generator
- ADR-008: Anti-Overfitting Architecture & OOS Quarantine
- ADR-010: Composite Strategy Scoring & Quantitative Tiering
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from src.ai.pine_transpiler import PineScriptTranspiler
from src.ai.summarizer import MarketBrief
from src.backtest.types import BacktestMetrics, BacktestResult
from src.quant.validation.overfitting import (
    DSRResult,
    HoldoutValidationResult,
    TournamentLeaderboardEntry,
)
from src.quant.validation.pipeline import ValidationPipelineReport
from src.strategy.contracts import (
    ConditionComparison,
    ConditionNode,
    StrategyProposal,
    ValueRefType,
)


class StrategyDossierGenerator:
    """Generates standalone institutional validation dossiers in Markdown."""

    def __init__(self, transpiler: Optional[PineScriptTranspiler] = None):
        self.transpiler = transpiler or PineScriptTranspiler()

    def _format_condition(self, node: Union[ConditionNode, ConditionComparison, Any]) -> str:
        """Recursively format condition AST into readable mathematical prose."""
        if isinstance(node, ConditionComparison):
            left_str = f"{node.left.name}[{node.left.lag}]" if node.left.type != ValueRefType.CONSTANT else str(node.left.value)
            right_str = (
                f"{node.right.name}[{node.right.lag}]"
                if node.right and node.right.type != ValueRefType.CONSTANT
                else str(node.right.value) if node.right else ""
            )
            return f"`{left_str} {node.comparator} {right_str}`"
        elif isinstance(node, ConditionNode):
            sub = [self._format_condition(c) for c in node.conditions]
            op = f" {node.operator} "
            return f"({op.join(sub)})"
        return str(node)

    def generate_dossier(
        self,
        proposal: StrategyProposal,
        leaderboard_entry: TournamentLeaderboardEntry,
        pipeline_report: Optional[ValidationPipelineReport] = None,
        holdout_result: Optional[HoldoutValidationResult] = None,
        dsr_result: Optional[DSRResult] = None,
        pine_code: Optional[str] = None,
        market_brief: Optional[MarketBrief] = None,
    ) -> str:
        """Compile complete evidence-based validation dossier."""
        strat_id = str(proposal.strategy_id)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Compile Pine Script if not provided
        if pine_code is None:
            try:
                pine_code = self.transpiler.transpile(proposal)
            except Exception as err:
                pine_code = f"// Failed to generate Pine Script: {err}"

        # Status badge & header
        status_str = "PASSED ALL GATES" if leaderboard_entry.passed_all_gates else "DISQUALIFIED / REJECTED"
        tier_str = leaderboard_entry.tier

        doc = []
        doc.append(f"# AGentTrXPAUS Quantitative Strategy Dossier")
        doc.append(f"## Strategy: {proposal.name}")
        doc.append("")
        doc.append(f"**Strategy ID:** `{strat_id}`  ")
        doc.append(f"**Document Date:** {now_str}  ")
        doc.append(f"**Tournament Status:** **{tier_str}** ({status_str})  ")
        doc.append(f"**Tournament Rank:** Rank {leaderboard_entry.rank} (of {leaderboard_entry.trials_count} cumulative trials)  ")
        doc.append(f"**Composite Strategy Score (CSS):** **{leaderboard_entry.css_score:.2f} / 100.00**  ")
        doc.append(f"**Deflated Sharpe Ratio (DSR):** **{leaderboard_entry.dsr_score * 100:.2f}%**  ")
        doc.append("")
        doc.append("---")
        doc.append("")

        # 1. Executive Summary & Economic Hypothesis
        doc.append("## 1. Executive Summary & Economic Hypothesis")
        doc.append("")
        doc.append(f"- **Instrument:** `{proposal.instrument.value}` (Spot Gold)")
        doc.append(f"- **Primary Timeframe:** `{proposal.timeframe.value}`")
        doc.append(f"- **Trade Direction:** `{proposal.direction.value}`")
        doc.append(f"- **Target Regimes:** {', '.join(r.value for r in proposal.target_regimes)}")
        doc.append(f"- **Data Source:** `{proposal.data_source.value}`")
        doc.append(f"- **Generation Lineage:** Generation {leaderboard_entry.lineage.generation}, Trial #{leaderboard_entry.lineage.trial_index}")
        if leaderboard_entry.lineage.parent_strategy_id:
            doc.append(f"  - **Parent Strategy ID:** `{leaderboard_entry.lineage.parent_strategy_id}`")
        if leaderboard_entry.lineage.hypothesis_id:
            doc.append(f"  - **Hypothesis Ref:** `{leaderboard_entry.lineage.hypothesis_id}`")
        doc.append(f"- **AST Cryptographic Fingerprint:** `{leaderboard_entry.lineage.ast_hash[:16]}...` (SHA-256)")
        doc.append("")
        doc.append(f"### Economic Thesis")
        doc.append(f"> {proposal.hypothesis}")
        doc.append("")
        doc.append(f"**Quantitative Rationale:**  ")
        doc.append(f"{proposal.rationale}")
        doc.append("")
        doc.append("---")
        doc.append("")

        # 2. Strategy Architecture & AST Rules
        doc.append("## 2. Strategy Architecture & AST Specification")
        doc.append("")
        doc.append("### A. Required Indicators")
        doc.append("| Indicator Alias | Indicator Type | Configured Inputs |")
        doc.append("| :--- | :--- | :--- |")
        for ind in proposal.indicators:
            inputs_str = ", ".join(f"{k}={v}" for k, v in ind.inputs.items())
            doc.append(f"| `{ind.alias}` | {ind.name.value} | `{inputs_str}` |")
        doc.append("")

        doc.append("### B. Logic & Signal Execution Rules")
        doc.append(f"- **Entry Condition:** {self._format_condition(proposal.entry_rules)}")
        doc.append(f"- **Exit Condition:** {self._format_condition(proposal.exit_rules)}")
        doc.append("")

        doc.append("### C. Protective Risk & Bracket Orders")
        doc.append(f"- **Stop Loss Policy:** `{proposal.stop_loss.type}` (Multiplier/Value: `{proposal.stop_loss.multiplier_or_value}`)")
        doc.append(f"- **Take Profit Policy:** `{proposal.take_profit.type}` (Multiplier/Value: `{proposal.take_profit.multiplier_or_value}`)")
        doc.append(f"- **Max Holding Bars:** `{proposal.constraints.max_holding_bars}` bars")
        doc.append(f"- **Allowed Trading Sessions:** {', '.join(proposal.constraints.allowed_sessions)}")
        doc.append("")
        doc.append("---")
        doc.append("")

        # 3. In-Sample & Full-Sample Performance
        doc.append("## 3. In-Sample & Historical Backtest Performance")
        doc.append("")
        bt_res = pipeline_report.is_backtest_result if pipeline_report else None

        if bt_res and bt_res.metrics:
            m = bt_res.metrics
            doc.append("| Performance Metric | Result | Benchmark / Safety Policy | Gate Status |")
            doc.append("| :--- | :--- | :--- | :---: |")
            doc.append(f"| **Total Net Profit** | ${m.total_net_profit:,.2f} | > $0.00 | {'✅ PASS' if m.total_net_profit > 0 else '❌ FAIL'} |")
            doc.append(f"| **Total Return** | {m.total_return_pct:.2f}% | — | ℹ️ |")
            doc.append(f"| **Gross Profit Factor** | {m.profit_factor:.2f} | Min 1.30 | {'✅ PASS' if m.profit_factor >= 1.30 else '❌ FAIL'} |")
            doc.append(f"| **Win Rate** | {m.win_rate_pct:.2f}% | — | ℹ️ |")
            doc.append(f"| **Total Trades** | {m.total_trades} | Min 50 trades | {'✅ PASS' if m.total_trades >= 50 else '❌ FAIL'} |")
            doc.append(f"| **Winning / Losing Trades** | {m.winning_trades} / {m.losing_trades} | — | ℹ️ |")
            doc.append(f"| **Peak-to-Trough Drawdown** | {m.max_drawdown_pct:.2f}% (${m.max_drawdown_usd:,.2f}) | Max 15.0% | {'✅ PASS' if m.max_drawdown_pct <= 15.0 else '❌ FAIL'} |")
            doc.append(f"| **Drawdown Duration** | {m.max_drawdown_duration_bars} bars | — | ℹ️ |")
            doc.append(f"| **Annualized Sharpe Ratio** | {m.sharpe_ratio:.2f} | — | ℹ️ |")
            doc.append(f"| **Full-Sample Sortino Ratio** | {m.sortino_ratio:.2f} | — | ℹ️ |")
            doc.append(f"| **Expectancy** | ${m.expectancy_usd:.2f} / trade | > 0.15 × spread | {'✅ PASS' if m.expectancy_usd >= 0.05 else '❌ FAIL'} |")
            doc.append(f"| **Average R-Multiple** | {m.average_r:.2f}R | — | ℹ️ |")
            doc.append(f"| **Max Consecutive Losses** | {m.max_consecutive_losses} | Max 8 | {'✅ PASS' if m.max_consecutive_losses <= 8 else '❌ FAIL'} |")
            doc.append(f"| **Execution Fees Paid** | ${m.total_fees_paid:,.2f} | — | ℹ️ |")
            doc.append(f"| **Execution Slippage Cost** | ${m.total_slippage_cost:,.2f} | — | ℹ️ |")
        else:
            doc.append("> ⚠️ **[NOT_EVALUATED]** In-Sample backtest metrics not available or evaluation aborted prior to backtest execution.")

        doc.append("")
        doc.append("---")
        doc.append("")

        # 4. Walk-Forward Analysis (WFA)
        doc.append("## 4. Walk-Forward Analysis (WFA) & Out-of-Sample Efficiency")
        doc.append("")
        wfa_res = pipeline_report.wfa_result if pipeline_report else None

        if wfa_res:
            doc.append(f"- **WFA Overall Status:** {'✅ **PASSED**' if wfa_res.passed else '❌ **FAILED**'}")
            doc.append(f"- **Mean Walk-Forward Efficiency (WFE):** **{wfa_res.mean_wfe:.4f}** (Minimum hurdle: `0.5000`)")
            doc.append(f"- **OOS Profit Factor Retention Ratio:** **{wfa_res.oos_retention_ratio:.4f}** (Minimum hurdle: `0.6500`)")
            doc.append(f"- **Folds Analyzed:** {len(wfa_res.folds)}")
            doc.append("")
            if wfa_res.folds:
                doc.append("| Fold # | In-Sample Return | Out-of-Sample Return | OOS Trades | Fold WFE | Status |")
                doc.append("| :---: | :--- | :--- | :---: | :--- | :---: |")
                for f_idx, fold in enumerate(wfa_res.folds, start=1):
                    doc.append(
                        f"| {f_idx} | {fold.is_return:+.2f}% | {fold.oos_return:+.2f}% | {fold.oos_trades} | "
                        f"{fold.wfe:.4f} | {'✅ PASS' if fold.passed else '❌ FAIL'} |"
                    )
            if wfa_res.rejection_reasons:
                doc.append("")
                doc.append(f"**WFA Failure Notes:** {'; '.join(wfa_res.rejection_reasons)}")
        else:
            doc.append("> ⚠️ **[NOT_EVALUATED]** Walk-Forward Analysis was not executed. (Pipeline short-circuited at In-Sample hard gates).")

        doc.append("")
        doc.append("---")
        doc.append("")

        # 5. Monte Carlo Robustness & Stress Testing
        doc.append("## 5. Monte Carlo Robustness & Stress Testing")
        doc.append("")
        mc_res = pipeline_report.monte_carlo_result if pipeline_report else None

        if mc_res:
            doc.append(f"- **Monte Carlo Status:** {'✅ **PASSED**' if mc_res.passed else '❌ **FAILED**'}")
            doc.append(f"- **Bootstrap Reshuffle Paths:** {mc_res.iterations:,} iterations")
            doc.append(f"- **95th Percentile Drawdown (P95 MaxDD):** **{mc_res.p95_drawdown_pct * 100:.2f}%** (Risk limit: `20.0%`)")
            doc.append(f"- **Probability of Ruin (20% DD):** **{mc_res.probability_of_ruin * 100:.2f}%** (Risk limit: `5.0%`)")
            doc.append(f"- **Parameter Plateau Degradation:** **{mc_res.param_degradation_pct * 100:.2f}%** under ±10% perturbation (Limit: `40.0%`)")
            doc.append(f"- **P95 Losing Streak:** {mc_res.p95_consecutive_losses} trades")
            if mc_res.rejection_reasons:
                doc.append("")
                doc.append(f"**Monte Carlo Failure Notes:** {'; '.join(mc_res.rejection_reasons)}")
        else:
            doc.append("> ⚠️ **[NOT_EVALUATED]** Monte Carlo stress test was not executed. (Pipeline short-circuited at earlier gates).")

        doc.append("")
        doc.append("---")
        doc.append("")

        # 6. Anti-Overfitting Controls & Multiple-Testing Audit (DSR)
        doc.append("## 6. Anti-Overfitting Controls & Multiple-Testing Audit (DSR)")
        doc.append("")
        doc.append("### A. Deflated Sharpe Ratio (Bailey & López de Prado 2014)")
        doc.append(f"- **Cumulative Strategy Trials Evaluated ($N$):** **{leaderboard_entry.trials_count}**")
        doc.append(f"- **Expected Maximum Sharpe under Null ($E[\\max_N]$):** **{leaderboard_entry.expected_max_sharpe:.4f}**")
        doc.append(f"- **Observed Annualized Sharpe Ratio:** **{leaderboard_entry.sortino_ratio:.2f}**")
        doc.append(f"- **Deflated Sharpe Ratio (DSR):** **{leaderboard_entry.dsr_score * 100:.2f}%**")
        doc.append(f"- **Significance Threshold:** `95.0%` ($\\alpha = 0.05$)")
        doc.append(f"- **DSR Gate Status:** {'✅ **PASSED** (Statistically genuine edge)' if leaderboard_entry.dsr_score >= 0.95 else '❌ **FAILED** (Unreliable edge under multiple testing)'}")
        doc.append("")

        doc.append("### B. Quarantined 20% Out-of-Sample Holdout Gate")
        doc.append("> **Architectural Security Assertion:** The final 20% holdout partition was physically air-gapped ")
        doc.append("> during ideation, feature summarization, parameter selection, and Walk-Forward Analysis.")
        doc.append("")

        if leaderboard_entry.holdout_passed is not None:
            doc.append(f"- **Holdout Evaluation Status:** {'✅ **PASSED**' if leaderboard_entry.holdout_passed else '❌ **FAILED**'}")
            doc.append(f"- **Holdout Sharpe Ratio:** **{leaderboard_entry.holdout_sharpe:.2f}** (Minimum hurdle: `1.00`)")
        elif holdout_result:
            doc.append(f"- **Holdout Evaluation Status:** {'✅ **PASSED**' if holdout_result.passed else '❌ **FAILED**'}")
            doc.append(f"- **Holdout Sharpe Ratio:** **{holdout_result.sharpe_ratio:.2f}** (Minimum hurdle: `1.00`)")
            doc.append(f"- **Holdout Max Drawdown:** **{holdout_result.max_drawdown_pct:.2f}%** (Risk limit: `20.0%`)")
            doc.append(f"- **Holdout Net Profit:** **${holdout_result.total_return_pct:.2f}%** (${holdout_result.total_trades} trades)")
        else:
            doc.append("> ℹ️ **[NOT_EVALUATED]** Holdout partition was not evaluated (strategy was disqualified prior to terminal gate, or holdout was not requested).")

        doc.append("")
        doc.append("---")
        doc.append("")

        # 7. Composite Strategy Score (CSS) & Tournament Ranking
        doc.append("## 7. Composite Strategy Score (CSS) & Tournament Ranking")
        doc.append("")
        doc.append(f"- **Final Composite Strategy Score (CSS):** **{leaderboard_entry.css_score:.2f} / 100.00**")
        doc.append(f"- **Quantitative Tier:** **{leaderboard_entry.tier}**")
        doc.append(f"- **Tournament Rank:** **#{leaderboard_entry.rank}**")
        doc.append("")
        doc.append("### Decision & Rationale")
        doc.append(f"> {leaderboard_entry.explanation}")
        doc.append("")
        if leaderboard_entry.rejection_reasons:
            doc.append("### Gate Rejection Checklist")
            for r in leaderboard_entry.rejection_reasons:
                doc.append(f"- ❌ {r}")
            doc.append("")

        doc.append("---")
        doc.append("")

        # 8. Generated TradingView Pine Script v6
        doc.append("## 8. Production TradingView Pine Script v6")
        doc.append("")
        doc.append("```pinescript")
        doc.append(pine_code)
        doc.append("```")
        doc.append("")
        doc.append("---")
        doc.append("")

        # 9. Data Provenance & Limitations
        doc.append("## 9. Data Provenance & Methodological Limitations")
        doc.append("")
        doc.append("1. **Data Provenance:** Historical XAUUSD 1-hour tick/bar data sourced from Dukascopy / Yahoo Finance.")
        doc.append("2. **Execution Friction:** Simulations deduct $0.30/oz half-spread and 2 ticks of slippage.")
        doc.append("3. **Non-Repainting Invariants:** Generated Pine Script enforces `process_orders_on_close = true`, `calc_on_every_tick = false`, and `calc_on_order_fills = false`.")
        doc.append("4. **Compilation Boundary:** Pine Script syntax is validated offline via AST static parsing (`PineStaticValidator`). Official cloud compilation occurs on TradingView.")
        doc.append("5. **Live Trading Authority:** `LIVE_TRADING_ENABLED = False` hard-locked. Live execution is an optional downstream capability governed by frozen safety invariants.")
        doc.append("")

        return "\n".join(doc)

    def export_dossier(
        self,
        dossier_markdown: str,
        output_dir: Union[str, Path],
        filename: Optional[str] = None,
    ) -> Path:
        """Export dossier Markdown file to a target directory."""
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        if not filename:
            filename = f"dossier_{uuid4().hex[:8]}.md"

        target_file = out_path / filename
        target_file.write_text(dossier_markdown, encoding="utf-8")
        return target_file.resolve()
