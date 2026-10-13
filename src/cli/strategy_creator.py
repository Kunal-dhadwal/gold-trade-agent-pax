"""Unified CLI for AGentTrXPAUS Gold AI Strategy Creator.

Orchestrates end-to-end strategy generation, cascading quantitative validation,
multiple-testing overfitting controls, tournament ranking, TradingView Pine Script v6
transpilation, and comprehensive validation dossier generation.

Governing specifications:
- Milestone 2.5.4: Unified CLI & Golden End-to-End Verification
- Milestone 2.5.3: Batch Strategy Factory & Validation Dossier Generator
- Milestone 2.5.2: Deflated Sharpe Ratio & Strategy Tournament
- Milestone 2.5.1: Pine Script v6 Transpiler & Parity Verification
- ADR-008: Anti-Overfitting Architecture & OOS Quarantine
- ADR-010: Composite Strategy Scoring & Quantitative Tiering
"""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.ai.providers import GeminiProvider, MockLLMProvider, OllamaProvider
from src.ai.report_generator import StrategyDossierGenerator
from src.ai.strategy_factory import BatchStrategyFactory, StrategyFactoryConfig
from src.core.config import settings
from src.core.logger import logger
from src.data.models import validate_candle_dataframe
from src.data.sources.local import LocalFileClient
from src.data.storage.parquet_manager import ParquetStorageManager
from src.strategy.contracts import InstrumentEnum, TimeframeEnum


def parse_instrument(val: str) -> InstrumentEnum:
    """Map string representation to supported InstrumentEnum."""
    clean = val.strip().upper()
    if clean in ("XAUUSD", "GOLD", "XAU"):
        return InstrumentEnum.XAUUSD
    if clean in ("GC=F", "GC_F", "GC"):
        return InstrumentEnum.GC_F
    if clean in ("PAXGUSDT", "PAXG"):
        return InstrumentEnum.PAXGUSDT
    try:
        return InstrumentEnum(clean)
    except ValueError:
        raise ValueError(
            f"Unsupported instrument '{val}'. Supported instruments: {[i.value for i in InstrumentEnum]}"
        )


def parse_timeframe(val: str) -> TimeframeEnum:
    """Map string representation to supported TimeframeEnum."""
    clean = val.strip().lower()
    for tf in TimeframeEnum:
        if tf.value == clean:
            return tf
    raise ValueError(
        f"Unsupported timeframe '{val}'. Supported timeframes: {[t.value for t in TimeframeEnum]}"
    )


def compute_file_sha256(file_path: Path) -> str:
    """Compute SHA-256 hash for dataset provenance verification."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()


def generate_synthetic_candles(
    instrument: str = "XAUUSD",
    timeframe: str = "1h",
    n_bars: int = 12000,
) -> pd.DataFrame:
    """Deterministic synthetic benchmark dataset for offline smoke testing.

    Clearly labeled as synthetic benchmark; NOT real-market evidence.
    """
    dates = pd.date_range("2023-01-01", periods=n_bars, freq="1h", tz=timezone.utc)
    base = 1950.0
    drift = np.linspace(0, 200, n_bars)
    cycle = np.sin(np.linspace(0, 80, n_bars)) * 12.0
    closes = base + drift + cycle
    df = pd.DataFrame(
        {
            "timestamp": dates,
            "open": closes - 0.5,
            "high": closes + 2.0,
            "low": closes - 2.0,
            "close": closes,
            "volume": np.full(n_bars, 1000.0),
            "source": "SYNTHETIC_BENCHMARK_SMOKE_TEST",
            "instrument": instrument,
        }
    )
    return validate_candle_dataframe(df)


def parse_yahoo_raw_json(file_path: Path, instrument: str = "XAUUSD") -> pd.DataFrame:
    """Parse immutable raw Yahoo Finance JSON payload into validated DataFrame."""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    chart = data.get("chart", {})
    result_list = chart.get("result", [])
    if not result_list:
        raise ValueError(f"No result found in Yahoo Finance chart JSON at {file_path}")

    result = result_list[0]
    timestamps = result.get("timestamp", [])
    quote = result.get("indicators", {}).get("quote", [{}])[0]

    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])
    volumes = quote.get("volume", [])

    records = []
    for i, ts in enumerate(timestamps):
        o = opens[i] if i < len(opens) else None
        h = highs[i] if i < len(highs) else None
        l = lows[i] if i < len(lows) else None
        c = closes[i] if i < len(closes) else None
        v = volumes[i] if i < len(volumes) else 0

        if None in (o, h, l, c):
            continue

        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        records.append(
            {
                "timestamp": dt,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v) if v is not None else 0.0,
                "source": "YAHOO_RAW_ARCHIVE",
                "instrument": instrument,
            }
        )

    df = pd.DataFrame(records)
    if df.empty:
        raise ValueError(f"No valid candles parsed from Yahoo Finance JSON {file_path}")
    return validate_candle_dataframe(df)


def load_market_data(
    instrument: str,
    timeframe: str,
    data_path: Optional[str] = None,
    use_synthetic: bool = False,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Load market data with rigorous provenance tracking.

    Returns:
        Tuple[pd.DataFrame, Dict[str, Any]]: (validated_dataframe, provenance_metadata)
    """
    # 1. Explicit Synthetic Request
    if use_synthetic or (data_path and data_path.strip().lower() == "synthetic"):
        logger.warning(
            "Using SYNTHETIC benchmark dataset (12,000 bars). "
            "This run is for pipeline/smoke testing and does NOT represent real-market evidence."
        )
        df = generate_synthetic_candles(instrument=instrument, timeframe=timeframe)
        provenance = {
            "data_source": "SYNTHETIC_BENCHMARK",
            "file_path": None,
            "file_sha256": None,
            "total_bars": len(df),
            "start_time": df["timestamp"].iloc[0].isoformat(),
            "end_time": df["timestamp"].iloc[-1].isoformat(),
            "is_synthetic": True,
            "instrument": instrument,
            "timeframe": timeframe,
        }
        return df, provenance

    # 2. Explicit File Path Provided
    if data_path:
        p = Path(data_path)
        if not p.exists():
            raise FileNotFoundError(f"Specified market data file does not exist: {p}")

        checksum = compute_file_sha256(p)
        suffix = p.suffix.lower()

        if suffix in (".parquet", ".pq"):
            df = LocalFileClient.load_file(p, instrument=instrument, source="LOCAL_PARQUET")
            data_source = "LOCAL_PARQUET"
        elif suffix in (".csv", ".txt"):
            df = LocalFileClient.load_file(p, instrument=instrument, source="LOCAL_CSV")
            data_source = "LOCAL_CSV"
        elif suffix == ".json":
            df = parse_yahoo_raw_json(p, instrument=instrument)
            data_source = "RAW_YAHOO_JSON"
        else:
            raise ValueError(f"Unsupported market data file format: {suffix}")

        provenance = {
            "data_source": data_source,
            "file_path": str(p.resolve()),
            "file_sha256": checksum,
            "total_bars": len(df),
            "start_time": df["timestamp"].iloc[0].isoformat(),
            "end_time": df["timestamp"].iloc[-1].isoformat(),
            "is_synthetic": False,
            "instrument": instrument,
            "timeframe": timeframe,
        }
        return df, provenance

    # 3. Default Repository Search
    # Check Parquet storage
    storage = ParquetStorageManager()
    sym_clean = instrument.replace("=", "_").replace("/", "_")
    norm_file = storage.normalized_dir / sym_clean / f"{timeframe}.parquet"
    tf_file = storage.timeframe_dir / sym_clean / f"{timeframe}.parquet"

    target_parquet = None
    if norm_file.exists():
        target_parquet = norm_file
    elif tf_file.exists():
        target_parquet = tf_file

    if target_parquet:
        try:
            df = pd.read_parquet(target_parquet)
            df = validate_candle_dataframe(df)
            if len(df) >= 200:
                checksum = compute_file_sha256(target_parquet)
                provenance = {
                    "data_source": "PARQUET_STORAGE",
                    "file_path": str(target_parquet.resolve()),
                    "file_sha256": checksum,
                    "total_bars": len(df),
                    "start_time": df["timestamp"].iloc[0].isoformat(),
                    "end_time": df["timestamp"].iloc[-1].isoformat(),
                    "is_synthetic": False,
                    "instrument": instrument,
                    "timeframe": timeframe,
                }
                return df, provenance
        except Exception as e:
            logger.warning(f"Failed to read existing Parquet file {target_parquet}: {e}")

    # Check raw Yahoo historical archive
    raw_yahoo = Path("data/raw/yahoo/raw_GC_F_1h_20261008_053213.json")
    if raw_yahoo.exists() and instrument in ("XAUUSD", "GC=F"):
        logger.info(f"Loading available historical Gold archive from: {raw_yahoo}")
        df = parse_yahoo_raw_json(raw_yahoo, instrument=instrument)
        checksum = compute_file_sha256(raw_yahoo)
        provenance = {
            "data_source": "RAW_YAHOO_ARCHIVE",
            "file_path": str(raw_yahoo.resolve()),
            "file_sha256": checksum,
            "total_bars": len(df),
            "start_time": df["timestamp"].iloc[0].isoformat(),
            "end_time": df["timestamp"].iloc[-1].isoformat(),
            "is_synthetic": False,
            "instrument": instrument,
            "timeframe": timeframe,
        }
        return df, provenance

    # 4. Nothing Found
    raise FileNotFoundError(
        f"No suitable historical market data found for {instrument} [{timeframe}].\n"
        f"Searched Parquet paths: {[str(norm_file), str(tf_file)]} and raw archive: {raw_yahoo}.\n"
        f"To provide data, use --data-path <file.parquet|file.csv|file.json>.\n"
        f"To run an offline smoke test, pass --use-synthetic."
    )


def build_summary_markdown(
    batch_id: str,
    instrument: str,
    timeframe: str,
    provider_name: str,
    provenance: Dict[str, Any],
    result_data: Dict[str, Any],
    output_dir: Path,
) -> str:
    """Construct an institutional Markdown run summary."""
    leaderboard = result_data.get("leaderboard", [])
    surviving = result_data.get("surviving_count", 0)
    rejected = result_data.get("rejected_count", 0)
    winner_name = result_data.get("winner_name")

    lines = [
        "# AGentTrXPAUS — Strategy Creator Golden Run Summary",
        "",
        f"**Batch ID:** `{batch_id}`  ",
        f"**Run Timestamp:** `{datetime.now(timezone.utc).isoformat()}`  ",
        f"**Instrument:** `{instrument}` | **Timeframe:** `{timeframe}` | **LLM Provider:** `{provider_name}`  ",
        "",
        "---",
        "",
        "## 1. Data Provenance & Boundary Evidence",
        "",
        "| Metric | Value |",
        "| :--- | :--- |",
        f"| **Data Source** | `{provenance.get('data_source')}` |",
        f"| **File Path** | `{provenance.get('file_path') or 'N/A (Generated Memory)'}` |",
        f"| **SHA-256 Checksum** | `{provenance.get('file_sha256') or 'N/A'}` |",
        f"| **Total Bars Loaded** | `{provenance.get('total_bars')} bars` |",
        f"| **Chronological Span** | `{provenance.get('start_time')} -> {provenance.get('end_time')}` |",
        f"| **Synthetic Data Flag** | `{'YES (BENCHMARK SMOKE)' if provenance.get('is_synthetic') else 'NO (REAL-MARKET ARCHIVE)'}` |",
        "| **Holdout Quarantine Ratio** | `20.0% (Chronologically Air-Gapped)` |",
        "",
        "---",
        "",
        "## 2. Tournament Leaderboard & Overfitting Controls",
        "",
        "| Rank | Strategy Name | Tier | Status | IS Sharpe | Profit Factor | Max DD | CSS Score | DSR | Holdout Sharpe | Rejection Reason |",
        "| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |",
    ]

    for entry in leaderboard:
        status_badge = "✅ PASSED" if entry.get("passed_all_gates") else "❌ REJECTED"
        reasons = entry.get("rejection_reasons", [])
        reason_str = "; ".join(reasons) if reasons else "None"
        if len(reason_str) > 60:
            reason_str = reason_str[:57] + "..."

        holdout_s = f"{entry.get('holdout_sharpe'):.2f}" if entry.get("holdout_sharpe") is not None else "[NOT_EVALUATED]"

        lines.append(
            f"| {entry.get('rank')} "
            f"| `{entry.get('strategy_name')}` "
            f"| `{entry.get('tier')}` "
            f"| {status_badge} "
            f"| {entry.get('sortino_ratio', 0.0):.2f} "
            f"| {entry.get('profit_factor', 0.0):.2f} "
            f"| {entry.get('max_drawdown_pct', 0.0):.2f}% "
            f"| {entry.get('css_score', 0.0):.1f} "
            f"| {entry.get('dsr_score', 0.0):.2f} "
            f"| {holdout_s} "
            f"| {reason_str} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 3. Executive Outcome",
        "",
        f"- **Candidates Requested:** {result_data.get('total_requested')}",
        f"- **Candidates Generated:** {result_data.get('total_generated')}",
        f"- **Surviving Strategies:** {surviving}",
        f"- **Rejected Strategies:** {rejected}",
    ])

    if winner_name:
        lines.append(f"- **Tournament Champion:** `{winner_name}` (Passed all quantitative and holdout gates)")
    else:
        lines.append(
            "- **Tournament Champion:** `None` (Zero candidates passed all mandatory gates; "
            "fail-closed institutional discipline preserved)"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 4. Exported Artifact Directory Structure",
        "",
        f"All outputs exported to: `{output_dir.resolve()}`",
        "",
        f"- **Run Summary:** `summary.md`, `summary.json`",
        f"- **Leaderboard:** `leaderboard.json`",
        f"- **Validation Dossiers:** `dossiers/*.md` ({len(result_data.get('dossiers', {}))} files)",
        f"- **TradingView Pine Script v6:** `pine/*.pine` ({len(result_data.get('pine_scripts', {}))} files)",
        "",
        "> [!IMPORTANT]",
        "> TradingView Pine Script v6 scripts require independent Pine Editor compilation verification ",
        "> on TradingView charts before any automated execution or alert integration.",
    ])

    return "\n".join(lines)


async def run_strategy_creator(args: argparse.Namespace) -> int:
    """Execute the unified strategy creator workflow."""
    start_time = datetime.now(timezone.utc)
    instrument_enum = parse_instrument(args.instrument)
    timeframe_enum = parse_timeframe(args.timeframe)
    output_dir = Path(args.output_dir)

    print("=" * 80)
    print("AGentTrXPAUS — Gold AI Strategy Creator (Milestone 2.5.4)")
    print("=" * 80)
    print(f"Instrument:        {instrument_enum.value}")
    print(f"Timeframe:         {timeframe_enum.value}")
    print(f"Candidates:        {args.candidates}")
    print(f"LLM Provider:      {args.provider.upper()}")
    print(f"Output Directory:  {output_dir}")
    print(f"Database Persist:  {args.persist_db}")
    print("=" * 80)

    # 1. Load Market Data
    try:
        raw_df, provenance = load_market_data(
            instrument=instrument_enum.value,
            timeframe=timeframe_enum.value,
            data_path=args.data_path,
            use_synthetic=args.use_synthetic,
        )
    except Exception as e:
        logger.error(f"Failed to load market data: {e}")
        print(f"\n[ERROR] Market data ingestion failed: {e}", file=sys.stderr)
        return 1

    print(f"[DATA] Loaded {provenance['total_bars']} bars from {provenance['data_source']}.")
    if provenance["is_synthetic"]:
        print("[DATA] NOTE: Synthetic benchmark dataset in use. Not real-market evidence.")

    # 2. Initialize Provider
    if args.provider.lower() == "mock":
        provider = MockLLMProvider()
    elif args.provider.lower() == "gemini":
        provider = GeminiProvider()
    elif args.provider.lower() == "ollama":
        provider = OllamaProvider()
    else:
        logger.error(f"Unknown LLM provider: {args.provider}")
        return 1

    # 3. Configure Batch Strategy Factory
    factory_config = StrategyFactoryConfig(
        instrument=instrument_enum,
        timeframes=[timeframe_enum],
        batch_size=args.candidates,
        min_dsr_threshold=args.min_dsr,
        require_holdout=args.require_holdout,
    )

    factory = BatchStrategyFactory(config=factory_config, provider=provider)
    batch_id = args.batch_id or f"gold_batch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    # 4. Execute Batch Generation & Cascading Validation
    print(f"\n[FACTORY] Executing batch '{batch_id}' ({args.candidates} candidates requested)...")
    try:
        batch_result = await factory.run_batch(raw_df=raw_df, batch_id=batch_id)
    except Exception as e:
        logger.error(f"Batch generation failed: {e}", exc_info=True)
        print(f"\n[ERROR] Batch generation encountered unhandled exception: {e}", file=sys.stderr)
        return 1

    # 5. Export Artifacts
    output_dir.mkdir(parents=True, exist_ok=True)
    dossiers_dir = output_dir / "dossiers"
    pine_dir = output_dir / "pine"
    dossiers_dir.mkdir(parents=True, exist_ok=True)
    pine_dir.mkdir(parents=True, exist_ok=True)

    dossier_generator = StrategyDossierGenerator()
    exported_dossiers: Dict[str, str] = {}
    exported_pine: Dict[str, str] = {}

    leaderboard_entries_map = {
        entry.strategy_id: entry for entry in batch_result.tournament_report.leaderboard
    }

    for strat_id, proposal in batch_result.proposals.items():
        leaderboard_entry = leaderboard_entries_map.get(strat_id)
        pipeline_rep = batch_result.pipeline_reports.get(strat_id)
        holdout_res = batch_result.holdout_results.get(strat_id)
        pine_code = batch_result.pine_scripts.get(strat_id)

        # Generate and save Dossier
        if leaderboard_entry:
            dossier_md = dossier_generator.generate_dossier(
                proposal=proposal,
                leaderboard_entry=leaderboard_entry,
                pipeline_report=pipeline_rep,
                holdout_result=holdout_res,
                pine_code=pine_code,
            )
            dossier_path = dossiers_dir / f"{proposal.name}_dossier.md"
            dossier_generator.export_dossier(dossier_md, dossiers_dir, f"{proposal.name}_dossier.md")
            exported_dossiers[proposal.name] = str(dossier_path)

        # Save Pine Script
        if pine_code:
            pine_path = pine_dir / f"{proposal.name}.pine"
            with open(pine_path, "w", encoding="utf-8") as f:
                f.write(pine_code)
            exported_pine[proposal.name] = str(pine_path)

    # 6. Build Summary & Leaderboard JSON
    leaderboard_dicts = [
        entry.to_dict() for entry in batch_result.tournament_report.leaderboard
    ]
    with open(output_dir / "leaderboard.json", "w", encoding="utf-8") as f:
        json.dump(leaderboard_dicts, f, indent=2, default=str)

    summary_data = {
        "batch_id": batch_id,
        "timestamp": start_time.isoformat(),
        "duration_seconds": round((datetime.now(timezone.utc) - start_time).total_seconds(), 2),
        "instrument": instrument_enum.value,
        "timeframe": timeframe_enum.value,
        "provider": args.provider,
        "total_requested": batch_result.total_requested,
        "total_generated": batch_result.total_generated,
        "surviving_count": batch_result.surviving_count,
        "rejected_count": batch_result.rejected_count,
        "winner_id": batch_result.winner_entry.strategy_id if batch_result.winner_entry else None,
        "winner_name": batch_result.winner_entry.strategy_name if batch_result.winner_entry else None,
        "data_provenance": provenance,
        "leaderboard": leaderboard_dicts,
        "dossiers": exported_dossiers,
        "pine_scripts": exported_pine,
        "generation_failures": batch_result.generation_failures,
    }

    with open(output_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, default=str)

    summary_md = build_summary_markdown(
        batch_id=batch_id,
        instrument=instrument_enum.value,
        timeframe=timeframe_enum.value,
        provider_name=args.provider.upper(),
        provenance=provenance,
        result_data=summary_data,
        output_dir=output_dir,
    )
    with open(output_dir / "summary.md", "w", encoding="utf-8") as f:
        f.write(summary_md)

    # 7. Optional Database Persistence
    if args.persist_db:
        print("\n[DATABASE] Persisting generated strategies to database...")
        try:
            from src.database.connection import AsyncSessionLocal, init_db

            await init_db()
            async with AsyncSessionLocal() as session:
                persisted_count = 0
                for strat_id, proposal in batch_result.proposals.items():
                    entry = leaderboard_entries_map.get(strat_id)
                    if entry:
                        p_rep = batch_result.pipeline_reports.get(strat_id)
                        p_code = batch_result.pine_scripts.get(strat_id)
                        await factory.persist_strategy_to_db(
                            db=session,
                            proposal=proposal,
                            entry=entry,
                            pine_code=p_code,
                            pipeline_report=p_rep,
                        )
                        persisted_count += 1
                await session.commit()
            print(f"[DATABASE] Successfully persisted {persisted_count} strategies into database.")
        except Exception as db_err:
            logger.error(f"Failed to persist strategies to database: {db_err}", exc_info=True)
            print(f"[DATABASE ERROR] Persistence failed: {db_err}", file=sys.stderr)

    # 8. Terminal Output Banner
    print("\n" + "=" * 80)
    print("TOURNAMENT RESULTS SUMMARY")
    print("=" * 80)
    print(f"Total Evaluated:   {batch_result.total_generated}")
    print(f"Surviving:         {batch_result.surviving_count}")
    print(f"Rejected:          {batch_result.rejected_count}")

    if batch_result.winner_entry:
        w = batch_result.winner_entry
        print(f"\nCHAMPION STRATEGY:  {w.strategy_name}")
        print(f"  Rank:             #{w.rank} ({w.tier})")
        print(f"  CSS Score:        {w.css_score:.1f}")
        print(f"  DSR Score:        {w.dsr_score:.2f}")
        print(f"  In-Sample Sharpe: {w.sortino_ratio:.2f}")
        print(f"  Profit Factor:    {w.profit_factor:.2f}")
        if w.holdout_sharpe is not None:
            print(f"  Holdout Sharpe:   {w.holdout_sharpe:.2f}")
    else:
        print("\nCHAMPION STRATEGY:  None")
        print("  Notice: Zero candidates passed all institutional validation gates.")
        print("  Fail-closed policy enforced; inspect dossiers for rejection rationales.")

    print("\n" + "=" * 80)
    print(f"Artifacts exported to: {output_dir.resolve()}")
    print(f"  - Summary:           {output_dir / 'summary.md'}")
    print(f"  - Leaderboard:       {output_dir / 'leaderboard.json'}")
    print(f"  - Dossiers:          {len(exported_dossiers)} files in {dossiers_dir}")
    print(f"  - Pine Script v6:    {len(exported_pine)} files in {pine_dir}")
    print("=" * 80 + "\n")

    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m src.cli.strategy_creator",
        description="AGentTrXPAUS — Unified Gold AI Strategy Creator & Tournament Engine",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--instrument",
        type=str,
        default="XAUUSD",
        help="Target trading instrument (XAUUSD, GC=F, PAXGUSDT)",
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        default="1h",
        help="Target timeframe (1m, 5m, 15m, 30m, 1h, 4h, 1d)",
    )
    parser.add_argument(
        "--candidates",
        type=int,
        default=5,
        help="Number of strategy candidates to generate in the batch",
    )
    parser.add_argument(
        "--provider",
        type=str,
        choices=["mock", "gemini", "ollama"],
        default="mock",
        help="AI/LLM provider abstraction for strategy research",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./output/strategies",
        help="Destination directory for dossiers, Pine Script v6 files, and run summaries",
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=["markdown", "json", "all"],
        default="markdown",
        help="Report export format",
    )
    parser.add_argument(
        "--persist-db",
        action="store_true",
        default=False,
        help="Persist generated strategies and backtest outcomes to database",
    )
    parser.add_argument(
        "--data-path",
        type=str,
        default=None,
        help="Path to historical market data (.parquet, .csv, .json), or 'synthetic'",
    )
    parser.add_argument(
        "--use-synthetic",
        action="store_true",
        default=False,
        help="Use deterministic synthetic benchmark dataset for offline smoke testing",
    )
    parser.add_argument(
        "--batch-id",
        type=str,
        default=None,
        help="Custom batch/run identifier",
    )
    parser.add_argument(
        "--min-dsr",
        type=float,
        default=0.95,
        help="Minimum Deflated Sharpe Ratio (DSR) threshold for Tier 1 qualification",
    )
    parser.add_argument(
        "--require-holdout",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enforce strict chronological 20%% holdout quarantine (ADR-008)",
    )
    return parser


def main() -> int:
    """CLI entrypoint."""
    parser = build_arg_parser()
    args = parser.parse_args()
    return asyncio.run(run_strategy_creator(args))


if __name__ == "__main__":
    sys.exit(main())
