"""Integration tests for PostgreSQL 16 runtime execution, migration, and safety invariants.

Executed when an active PostgreSQL 16 database is reachable via TEST_POSTGRESQL_URL or localhost:5432.
Provides explicit diagnostics if the PostgreSQL daemon is unavailable.
Strictly restricted to database 'gold_trading_test'.
"""

import os
import subprocess
import sys
from urllib.parse import urlparse
import pytest
from sqlalchemy import create_engine, inspect, text


def normalize_to_sync_url(url: str) -> str:
    """Normalize asynchronous or generic PostgreSQL URL to synchronous psycopg2 driver URL.

    Converts 'postgresql+asyncpg://' or 'postgresql://' to 'postgresql+psycopg2://'.
    Preserves already normalized 'postgresql+psycopg2://' URLs.
    """
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def normalize_to_async_url(url: str) -> str:
    """Normalize synchronous or generic PostgreSQL URL to asynchronous asyncpg driver URL.

    Converts 'postgresql+psycopg2://' or 'postgresql://' to 'postgresql+asyncpg://'.
    Preserves already normalized 'postgresql+asyncpg://' URLs.
    """
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def validate_test_database_target(url: str) -> None:
    """Enforce that integration tests ONLY target database 'gold_trading_test'.

    Prevents accidental execution or destructive operations against
    production ('gold_trading') or any other non-test database.
    """
    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/")
    if db_name != "gold_trading_test":
        raise ValueError(
            f"SAFETY VIOLATION: Integration tests must target database 'gold_trading_test', "
            f"got database name: '{db_name}'. Aborting destructive test operations."
        )


def check_postgresql_reachability() -> tuple[bool, str]:
    """Check if PostgreSQL server is available and return status with diagnostic details.

    Returns:
        tuple[bool, str]: (is_reachable, diagnostic_message)
    """
    raw_url = os.environ.get(
        "TEST_POSTGRESQL_URL",
        "postgresql+asyncpg://postgres:postgrespassword@127.0.0.1:5432/gold_trading_test",
    )
    sync_url = normalize_to_sync_url(raw_url)
    try:
        validate_test_database_target(sync_url)
        engine = create_engine(sync_url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1;"))
        engine.dispose()
        return True, "PostgreSQL reachable and responding to queries."
    except Exception as exc:
        diag = f"PostgreSQL connection check failed for {sync_url}: {type(exc).__name__}: {exc}"
        return False, diag


def get_verified_test_urls() -> tuple[str, str]:
    """Retrieve verified async and sync database URLs strictly targeting gold_trading_test."""
    raw_url = os.environ.get(
        "TEST_POSTGRESQL_URL",
        "postgresql+asyncpg://postgres:postgrespassword@127.0.0.1:5432/gold_trading_test",
    )
    async_url = normalize_to_async_url(raw_url)
    sync_url = normalize_to_sync_url(raw_url)
    validate_test_database_target(async_url)
    validate_test_database_target(sync_url)
    return async_url, sync_url


def run_alembic_subprocess(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    """Execute an Alembic subprocess command with mandatory pre-flight database target validation.

    Guarantees that validate_test_database_target() is evaluated immediately before every
    potentially mutating or destructive Alembic invocation.
    """
    target_url = env.get("DATABASE_URL", "")
    validate_test_database_target(target_url)
    return subprocess.run(
        [sys.executable, "-m", "alembic"] + args,
        env=env,
        capture_output=True,
        text=True,
    )


POSTGRES_REACHABLE, POSTGRES_DIAGNOSTIC = check_postgresql_reachability()
POSTGRES_REASON = (
    f"PostgreSQL 16 daemon is not reachable: {POSTGRES_DIAGNOSTIC}. "
    "Requires active PostgreSQL 16 container or server instance targeting 'gold_trading_test'."
)


# =============================================================================
# Unconditional Unit Tests for URL Normalization and Safety Guards
# =============================================================================


def test_url_normalization_sync():
    """Verify that asynchronous and generic URLs are correctly normalized to psycopg2 sync URLs."""
    async_url = "postgresql+asyncpg://postgres:pass@127.0.0.1:5432/gold_trading_test"
    generic_url = "postgresql://postgres:pass@127.0.0.1:5432/gold_trading_test"
    sync_url = "postgresql+psycopg2://postgres:pass@127.0.0.1:5432/gold_trading_test"

    assert normalize_to_sync_url(async_url) == sync_url
    assert normalize_to_sync_url(generic_url) == sync_url
    assert normalize_to_sync_url(sync_url) == sync_url


def test_url_normalization_async():
    """Verify that synchronous and generic URLs are correctly normalized to asyncpg async URLs."""
    sync_url = "postgresql+psycopg2://postgres:pass@127.0.0.1:5432/gold_trading_test"
    generic_url = "postgresql://postgres:pass@127.0.0.1:5432/gold_trading_test"
    async_url = "postgresql+asyncpg://postgres:pass@127.0.0.1:5432/gold_trading_test"

    assert normalize_to_async_url(sync_url) == async_url
    assert normalize_to_async_url(generic_url) == async_url
    assert normalize_to_async_url(async_url) == async_url


def test_database_name_safety_guard_accepts_test_db():
    """Verify that the safety guard accepts valid gold_trading_test URLs."""
    validate_test_database_target("postgresql+psycopg2://user:pass@host:5432/gold_trading_test")
    validate_test_database_target("postgresql+asyncpg://user:pass@host:5432/gold_trading_test")


def test_database_name_safety_guard_rejects_production_db():
    """Verify that the safety guard rejects production or non-test databases."""
    with pytest.raises(ValueError, match="SAFETY VIOLATION"):
        validate_test_database_target("postgresql+psycopg2://user:pass@host:5432/gold_trading")

    with pytest.raises(ValueError, match="SAFETY VIOLATION"):
        validate_test_database_target("postgresql+asyncpg://user:pass@host:5432/production_db")


def test_reachability_diagnostics_structure():
    """Verify that reachability check returns a boolean and informative diagnostic string."""
    is_up, diag = check_postgresql_reachability()
    assert isinstance(is_up, bool)
    assert isinstance(diag, str)
    assert len(diag) > 0


def test_run_alembic_subprocess_guards_against_unsafe_target():
    """Verify that run_alembic_subprocess enforces safety guard before invocation."""
    unsafe_env = {"DATABASE_URL": "postgresql+asyncpg://postgres:pass@localhost:5432/gold_trading"}
    with pytest.raises(ValueError, match="SAFETY VIOLATION"):
        run_alembic_subprocess(["upgrade", "head"], env=unsafe_env)


# =============================================================================
# Live PostgreSQL 16 Integration Tests
# =============================================================================


@pytest.mark.skipif(not POSTGRES_REACHABLE, reason=POSTGRES_REASON)
def test_postgresql_migration_upgrade_and_schema_verification():
    """Verify clean Alembic upgrade to head against live PostgreSQL 16 database."""
    async_url, sync_url = get_verified_test_urls()

    env = os.environ.copy()
    env["DATABASE_URL"] = async_url
    env["TEST_POSTGRESQL_URL"] = async_url

    # 1. Apply Alembic migration with pre-flight safety check
    res = run_alembic_subprocess(["upgrade", "head"], env=env)
    assert res.returncode == 0, f"PostgreSQL migration upgrade failed: {res.stderr}\n{res.stdout}"

    # 2. Inspect tables and foreign keys
    engine = create_engine(sync_url)
    insp = inspect(engine)

    tables = set(insp.get_table_names())
    expected_tables = {
        "live_accounts",
        "account_fee_schedules",
        "live_orders",
        "live_order_events",
        "leader_leases",
        "reconciliation_snapshots",
        "live_funding_events",
        "operator_reconciliation_audit_logs",
        "consumed_authorization_nonces",
        "account_equity_high_water_mark",
    }
    assert expected_tables.issubset(tables), f"Missing tables in PostgreSQL: {expected_tables - tables}"

    # 3. Verify XOR check constraint on positions
    check_constraints = insp.get_check_constraints("positions")
    chk_names = {c["name"] for c in check_constraints}
    assert "chk_positions_account_exclusivity" in chk_names, "Missing XOR exclusivity constraint"

    # 4. Verify account foreign keys on positions
    fks = insp.get_foreign_keys("positions")
    fk_names = {fk["name"] for fk in fks}
    assert "fk_positions_paper_account_id" in fk_names, "Missing paper account FK on positions"
    assert "fk_positions_live_account_id" in fk_names, "Missing live account FK on positions"

    engine.dispose()


@pytest.mark.skipif(not POSTGRES_REACHABLE, reason=POSTGRES_REASON)
def test_postgresql_rollback_preconditions_and_downgrade():
    """Verify rollback preconditions and clean downgrade on PostgreSQL database."""
    async_url, sync_url = get_verified_test_urls()

    env = os.environ.copy()
    env["DATABASE_URL"] = async_url
    env["TEST_POSTGRESQL_URL"] = async_url

    # 1. Self-contained setup: Ensure database is upgraded to head first
    res_setup = run_alembic_subprocess(["upgrade", "head"], env=env)
    assert res_setup.returncode == 0, f"Setup upgrade failed: {res_setup.stderr}"

    engine = create_engine(sync_url)

    # Clean up any residual test data from previous runs
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM positions WHERE id = 'POS_PG_TEST';"))
        conn.execute(text("DELETE FROM live_accounts WHERE account_id = 'ACC_PG_TEST';"))
        conn.execute(text("DELETE FROM instruments WHERE id = 'INST_PG_TEST';"))

    try:
        # 2. Seed test instrument and live account
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO instruments (
                    id, symbol, name, asset_class, quote_currency, tick_size, lot_size,
                    contract_multiplier, maker_fee_rate, taker_fee_rate, is_active, created_at, updated_at
                ) VALUES (
                    'INST_PG_TEST', 'XAUUSD', 'Gold Perpetual Test', 'COMMODITY', 'USDT',
                    0.01, 0.001, 1.0, 0.0002, 0.0005, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                ) ON CONFLICT (id) DO NOTHING;
            """))
            conn.execute(text("""
                INSERT INTO live_accounts (
                    account_id, name, currency, initial_balance, current_cash, status, created_at, updated_at
                ) VALUES (
                    'ACC_PG_TEST', 'PG Test Account', 'USDT', 1000.0, 1000.0, 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                ) ON CONFLICT (account_id) DO NOTHING;
            """))

        # 3. Test Precondition 1: Downgrade must abort when live positions exist
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO positions (
                    id, account_id, live_account_id, instrument_id, mode, direction,
                    quantity, average_entry_price, current_mark_price, unrealized_pnl, allocated_margin, updated_at
                ) VALUES (
                    'POS_PG_TEST', 'ACC_PG_TEST', 'ACC_PG_TEST', 'INST_PG_TEST', 'LIVE', 'LONG',
                    1.0, 2600.0, 2610.0, 10.0, 50.0, CURRENT_TIMESTAMP
                );
            """))

        res_abort_pos = run_alembic_subprocess(["downgrade", "-1"], env=env)
        assert res_abort_pos.returncode != 0, "Downgrade should have aborted due to active live positions"
        output_pos = res_abort_pos.stderr + res_abort_pos.stdout
        assert "ROLLBACK PRECONDITION VIOLATION" in output_pos
        assert "live position(s) exist" in output_pos

        # Remove the live position to clear Precondition 1
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM positions WHERE id = 'POS_PG_TEST';"))

        # 4. Test Precondition 2: Downgrade must abort when live_accounts is populated
        res_abort_tbl = run_alembic_subprocess(["downgrade", "-1"], env=env)
        assert res_abort_tbl.returncode != 0, "Downgrade should have aborted due to populated live_accounts"
        output_tbl = res_abort_tbl.stderr + res_abort_tbl.stdout
        assert "ROLLBACK PRECONDITION VIOLATION" in output_tbl
        assert "table 'live_accounts' contains 1 persistent row(s)" in output_tbl

        # Clean up live account and instrument
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM live_accounts WHERE account_id = 'ACC_PG_TEST';"))
            conn.execute(text("DELETE FROM instruments WHERE id = 'INST_PG_TEST';"))

        # 5. Downgrade must succeed when tables are empty
        res_down = run_alembic_subprocess(["downgrade", "-1"], env=env)
        assert res_down.returncode == 0, f"Clean downgrade failed: {res_down.stderr}\n{res_down.stdout}"

    finally:
        # 6. Teardown: Re-upgrade to head so database is restored to canonical state
        res_teardown = run_alembic_subprocess(["upgrade", "head"], env=env)
        engine.dispose()
        assert res_teardown.returncode == 0, (
            f"Teardown re-upgrade to head failed (exit code {res_teardown.returncode}):\n"
            f"{res_teardown.stderr}\n{res_teardown.stdout}"
        )
