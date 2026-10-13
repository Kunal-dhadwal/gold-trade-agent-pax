"""Integration tests for the Unified Strategy Creator CLI (Milestone 2.5.4).

Verifies end-to-end orchestration, CLI argument parsing, provenance recording,
safe handling of zero eligible candidates on historical data, synthetic smoke runs,
and database persistence.
"""

import json
from pathlib import Path
import pytest
from sqlalchemy import select

from src.cli.strategy_creator import (
    build_arg_parser,
    load_market_data,
    run_strategy_creator,
)
from src.database.connection import AsyncSessionLocal
from src.database.models import Strategy, StrategyVersion


def test_cli_argument_parser():
    """Verify CLI argument defaults and flag parsing."""
    parser = build_arg_parser()
    args = parser.parse_args([])

    assert args.instrument == "XAUUSD"
    assert args.timeframe == "1h"
    assert args.candidates == 5
    assert args.provider == "mock"
    assert args.output_dir == "./output/strategies"
    assert args.format == "markdown"
    assert args.persist_db is False
    assert args.use_synthetic is False
    assert args.require_holdout is True

    # Custom flags
    custom_args = parser.parse_args(
        [
            "--instrument", "GC=F",
            "--timeframe", "15m",
            "--candidates", "3",
            "--provider", "gemini",
            "--output-dir", "/tmp/custom_output",
            "--persist-db",
            "--use-synthetic",
            "--no-require-holdout",
        ]
    )
    assert custom_args.instrument == "GC=F"
    assert custom_args.timeframe == "15m"
    assert custom_args.candidates == 3
    assert custom_args.provider == "gemini"
    assert custom_args.output_dir == "/tmp/custom_output"
    assert custom_args.persist_db is True
    assert custom_args.use_synthetic is True
    assert custom_args.require_holdout is False


def test_data_loader_real_market_provenance():
    """Data loader properly records real market file provenance and checksum."""
    real_path = "data/raw/yahoo/raw_GC_F_1h_20261008_053213.json"
    assert Path(real_path).exists(), "Real Yahoo Gold archive must exist in repository."

    df, provenance = load_market_data(
        instrument="XAUUSD",
        timeframe="1h",
        data_path=real_path,
        use_synthetic=False,
    )

    assert len(df) == 510
    assert provenance["is_synthetic"] is False
    assert provenance["data_source"] == "RAW_YAHOO_JSON"
    assert provenance["file_sha256"] is not None
    assert len(provenance["file_sha256"]) == 64
    assert provenance["total_bars"] == 510


def test_data_loader_synthetic_benchmark_provenance():
    """Data loader properly marks synthetic benchmark data as synthetic."""
    df, provenance = load_market_data(
        instrument="XAUUSD",
        timeframe="1h",
        use_synthetic=True,
    )

    assert len(df) == 12000
    assert provenance["is_synthetic"] is True
    assert provenance["data_source"] == "SYNTHETIC_BENCHMARK"
    assert provenance["file_path"] is None


@pytest.mark.asyncio
async def test_cli_golden_run_real_market_archive(tmp_path):
    """End-to-end golden verification run using real historical Gold data.

    On 510 bars, candidates cannot satisfy the 50 minimum trades gate.
    Verifies safe handling of 0 surviving candidates, honest rejection,
    leaderboard generation, Pine Script v6 export, and dossier export.
    """
    output_dir = tmp_path / "strategies_real"
    parser = build_arg_parser()
    args = parser.parse_args(
        [
            "--instrument", "XAUUSD",
            "--timeframe", "1h",
            "--candidates", "5",
            "--provider", "mock",
            "--output-dir", str(output_dir),
            "--format", "markdown",
            "--data-path", "data/raw/yahoo/raw_GC_F_1h_20261008_053213.json",
        ]
    )

    exit_code = await run_strategy_creator(args)
    assert exit_code == 0

    # 1. Verify Directory & File Structure
    assert (output_dir / "summary.json").exists()
    assert (output_dir / "summary.md").exists()
    assert (output_dir / "leaderboard.json").exists()

    dossiers_dir = output_dir / "dossiers"
    pine_dir = output_dir / "pine"
    assert dossiers_dir.exists()
    assert pine_dir.exists()

    dossier_files = list(dossiers_dir.glob("*.md"))
    pine_files = list(pine_dir.glob("*.pine"))
    assert len(dossier_files) == 5
    assert len(pine_files) == 5

    # 2. Verify summary.json content and provenance
    with open(output_dir / "summary.json", "r", encoding="utf-8") as f:
        summary_data = json.load(f)

    assert summary_data["total_requested"] == 5
    assert summary_data["total_generated"] == 5
    assert summary_data["surviving_count"] == 0
    assert summary_data["rejected_count"] == 5
    assert summary_data["winner_id"] is None
    assert summary_data["data_provenance"]["is_synthetic"] is False
    assert summary_data["data_provenance"]["total_bars"] == 510
    assert len(summary_data["leaderboard"]) == 5

    # 3. Verify summary.md content
    with open(output_dir / "summary.md", "r", encoding="utf-8") as f:
        summary_md = f.read()

    assert "AGentTrXPAUS — Strategy Creator Golden Run Summary" in summary_md
    assert "RAW_YAHOO_JSON" in summary_md or "RAW_YAHOO_ARCHIVE" in summary_md
    assert "Tournament Leaderboard" in summary_md
    assert "Zero candidates passed all mandatory gates" in summary_md

    # 4. Verify Pine Script v6 files
    for pf in pine_files:
        with open(pf, "r", encoding="utf-8") as f:
            p_code = f.read()
        assert "//@version=6" in p_code
        assert "strategy(" in p_code


@pytest.mark.asyncio
async def test_cli_golden_run_synthetic_benchmark(tmp_path):
    """End-to-end smoke verification using synthetic benchmark data."""
    output_dir = tmp_path / "strategies_synth"
    parser = build_arg_parser()
    args = parser.parse_args(
        [
            "--instrument", "XAUUSD",
            "--timeframe", "1h",
            "--candidates", "3",
            "--provider", "mock",
            "--output-dir", str(output_dir),
            "--format", "markdown",
            "--use-synthetic",
        ]
    )

    exit_code = await run_strategy_creator(args)
    assert exit_code == 0

    assert (output_dir / "summary.json").exists()
    with open(output_dir / "summary.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["total_requested"] == 3
    assert data["total_generated"] == 3
    assert data["data_provenance"]["is_synthetic"] is True
    assert len(list((output_dir / "dossiers").glob("*.md"))) == 3
    assert len(list((output_dir / "pine").glob("*.pine"))) == 3


@pytest.mark.asyncio
async def test_cli_golden_run_database_persistence(tmp_path):
    """Verifies that generated candidates and versions persist to database when requested."""
    output_dir = tmp_path / "strategies_db"
    parser = build_arg_parser()
    args = parser.parse_args(
        [
            "--instrument", "XAUUSD",
            "--timeframe", "1h",
            "--candidates", "2",
            "--provider", "mock",
            "--output-dir", str(output_dir),
            "--format", "markdown",
            "--use-synthetic",
            "--persist-db",
        ]
    )

    exit_code = await run_strategy_creator(args)
    assert exit_code == 0

    # Query DB to ensure records were written
    async with AsyncSessionLocal() as session:
        strats = (await session.execute(select(Strategy))).scalars().all()
        versions = (await session.execute(select(StrategyVersion))).scalars().all()

        assert len(strats) >= 2
        assert len(versions) >= 2


@pytest.mark.asyncio
async def test_cli_missing_market_data_error(tmp_path):
    """CLI exits with code 1 and logs error when data file is not found."""
    output_dir = tmp_path / "strategies_err"
    parser = build_arg_parser()
    args = parser.parse_args(
        [
            "--instrument", "XAUUSD",
            "--timeframe", "1h",
            "--candidates", "2",
            "--output-dir", str(output_dir),
            "--data-path", "data/non_existent_file.parquet",
        ]
    )

    exit_code = await run_strategy_creator(args)
    assert exit_code == 1

