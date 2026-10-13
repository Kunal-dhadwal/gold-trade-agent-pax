"""Integration tests for Milestone 4.2 database migration, orphan detection, backfill, and rollback safety."""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import pytest


@pytest.fixture
def isolated_db_env():
    """Create a temporary directory and SQLite database URL for isolated migration testing."""
    temp_dir = tempfile.mkdtemp()
    temp_db_path = os.path.join(temp_dir, "test_migration.db")
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{temp_db_path}"

    yield {
        "dir": temp_dir,
        "db_path": temp_db_path,
        "env": env,
    }

    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


def test_clean_upgrade_and_downgrade_on_empty_db(isolated_db_env):
    """Verify that migration applies cleanly on empty DB and rolls back cleanly when empty."""
    env = isolated_db_env["env"]
    db_path = isolated_db_env["db_path"]

    # 1. Apply all migrations up to head (including 5c6a7b8e9f01)
    res_up = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_up.returncode == 0, f"Upgrade failed: {res_up.stderr}"

    # Verify that all 10 live execution tables exist
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in c.fetchall()}

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
    assert expected_tables.issubset(tables), f"Missing tables: {expected_tables - tables}"

    # Verify positions columns
    c.execute("PRAGMA table_info(positions)")
    pos_cols = {row[1] for row in c.fetchall()}
    assert "paper_account_id" in pos_cols
    assert "live_account_id" in pos_cols
    conn.close()

    # 2. Downgrade -1 (must succeed cleanly since all tables are empty)
    res_down = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "-1"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down.returncode == 0, f"Downgrade failed: {res_down.stderr}"

    # Verify that the 10 live tables are removed
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    remaining_tables = {row[0] for row in c.fetchall()}
    for tbl in expected_tables:
        assert tbl not in remaining_tables, f"Table '{tbl}' was not dropped during rollback"

    # Verify positions columns reverted
    c.execute("PRAGMA table_info(positions)")
    reverted_cols = {row[1] for row in c.fetchall()}
    assert "paper_account_id" not in reverted_cols
    assert "live_account_id" not in reverted_cols
    conn.close()


def test_pre_migration_orphan_detection_aborts(isolated_db_env):
    """Verify that migration detects orphaned positions and aborts without altering schema."""
    env = isolated_db_env["env"]
    db_path = isolated_db_env["db_path"]

    # 1. Upgrade to pre-Milestone-4.2 revision (4b591a2e8c92)
    res_pre = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "4b591a2e8c92"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_pre.returncode == 0, f"Pre-migration upgrade failed: {res_pre.stderr}"

    # 2. Insert an orphaned position referencing non-existent account ID
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        INSERT INTO positions (
            id, account_id, instrument_id, mode, direction, quantity,
            average_entry_price, current_mark_price, unrealized_pnl, allocated_margin, updated_at
        ) VALUES (
            'pos_orphan_test', 'NON_EXISTENT_ACC', 'XAUUSD', 'PAPER', 'LONG', 1.0,
            2000.0, 2000.0, 0.0, 0.0, datetime('now')
        )
    """)
    conn.commit()
    conn.close()

    # 3. Attempt upgrade to head -> must abort with PRE-MIGRATION INTEGRITY ERROR
    res_up = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_up.returncode != 0, "Upgrade should have failed on orphaned position"
    output = res_up.stderr + res_up.stdout
    assert "PRE-MIGRATION INTEGRITY ERROR" in output
    assert "Detected 1 orphaned or invalid position record(s)" in output

    # 4. Verify that schema changes were NOT applied (transaction rolled back)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='live_accounts'")
    assert c.fetchone() is None, "live_accounts should not exist after aborted migration"
    conn.close()


def test_deterministic_backfill_and_exclusivity_constraint(isolated_db_env):
    """Verify legacy data backfill and chk_positions_account_exclusivity enforcement."""
    env = isolated_db_env["env"]
    db_path = isolated_db_env["db_path"]

    # 1. Upgrade to pre-Milestone-4.2 revision
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "4b591a2e8c92"],
        env=env,
        check=True,
        capture_output=True,
    )

    # 2. Seed paper account and legacy position
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        INSERT INTO paper_accounts (id, name, currency, initial_balance, current_cash, status, created_at, updated_at)
        VALUES ('ACC_PAP_BACKFILL', 'Paper Backfill Test', 'USDT', 10000.0, 10000.0, 'ACTIVE', datetime('now'), datetime('now'))
    """)
    c.execute("""
        INSERT INTO positions (
            id, account_id, instrument_id, mode, direction, quantity,
            average_entry_price, current_mark_price, unrealized_pnl, allocated_margin, updated_at
        ) VALUES (
            'pos_legacy_backfill', 'ACC_PAP_BACKFILL', 'XAUUSD', 'PAPER', 'LONG', 2.0,
            2600.0, 2610.0, 20.0, 100.0, datetime('now')
        )
    """)
    conn.commit()
    conn.close()

    # 3. Upgrade to head
    res_up = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_up.returncode == 0, f"Upgrade failed: {res_up.stderr}"

    # 4. Verify deterministic backfill populated paper_account_id and left live_account_id null
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(
        "SELECT id, account_id, paper_account_id, live_account_id, mode FROM positions WHERE id = 'pos_legacy_backfill'"
    )
    row = c.fetchone()
    assert row is not None
    assert row[1] == "ACC_PAP_BACKFILL"
    assert row[2] == "ACC_PAP_BACKFILL"  # paper_account_id backfilled
    assert row[3] is None  # live_account_id is null
    assert row[4] == "PAPER"

    # 5. Verify chk_positions_account_exclusivity rejects ambiguous configuration
    # (mode='PAPER' with live_account_id set)
    with pytest.raises(sqlite3.IntegrityError) as exc_info:
        c.execute("""
            INSERT INTO positions (
                id, account_id, paper_account_id, live_account_id, instrument_id, mode, direction,
                quantity, average_entry_price, current_mark_price, unrealized_pnl, allocated_margin, updated_at
            ) VALUES (
                'pos_invalid_exclusivity', 'ACC_PAP_BACKFILL', 'ACC_PAP_BACKFILL', 'ACC_LIVE_DUMMY',
                'XAUUSD', 'PAPER', 'LONG', 1.0, 2000.0, 2000.0, 0.0, 0.0, datetime('now')
            )
        """)
        conn.commit()
    assert "CHECK constraint failed: chk_positions_account_exclusivity" in str(exc_info.value)

    conn.close()


def test_downgrade_aborts_when_live_table_populated(isolated_db_env):
    """Verify that rollback aborts with non-zero exit code when any Milestone 4.2 table has records."""
    env = isolated_db_env["env"]
    db_path = isolated_db_env["db_path"]

    # 1. Upgrade to head
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        check=True,
        capture_output=True,
    )

    # 2. Insert record into live_accounts
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        INSERT INTO live_accounts (account_id, name, currency, initial_balance, current_cash, status, created_at, updated_at)
        VALUES ('ACC_LIVE_AUDIT_SAFE', 'Audit Safe Account', 'USDT', 50000.0, 50000.0, 'ACTIVE', datetime('now'), datetime('now'))
    """)
    conn.commit()
    conn.close()

    # 3. Attempt downgrade -1 -> must abort
    res_down = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "-1"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down.returncode != 0, "Downgrade should have aborted on populated table"
    output = res_down.stderr + res_down.stdout
    assert "ROLLBACK PRECONDITION VIOLATION" in output
    assert "table 'live_accounts' contains 1 persistent row(s)" in output

    # 4. Verify that schema remains intact (no tables dropped)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='live_accounts'")
    assert c.fetchone() is not None, "live_accounts should still exist after aborted downgrade"
    conn.close()


def test_downgrade_aborts_when_live_positions_exist(isolated_db_env):
    """Verify that rollback aborts when positions table contains live positions."""
    env = isolated_db_env["env"]
    db_path = isolated_db_env["db_path"]

    # 1. Upgrade to head
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        check=True,
        capture_output=True,
    )

    # 2. Insert live account and live position
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        INSERT INTO live_accounts (account_id, name, currency, initial_balance, current_cash, status, created_at, updated_at)
        VALUES ('ACC_LIVE_POS_01', 'Live Pos Account', 'USDT', 25000.0, 25000.0, 'ACTIVE', datetime('now'), datetime('now'))
    """)
    c.execute("""
        INSERT INTO positions (
            id, account_id, live_account_id, instrument_id, mode, direction, quantity,
            average_entry_price, current_mark_price, unrealized_pnl, allocated_margin, updated_at
        ) VALUES (
            'pos_live_abort_test', 'ACC_LIVE_POS_01', 'ACC_LIVE_POS_01', 'XAUUSD', 'LIVE', 'LONG', 1.0,
            2700.0, 2705.0, 5.0, 50.0, datetime('now')
        )
    """)
    conn.commit()
    conn.close()

    # 3. Attempt downgrade -1 -> must abort on live positions check
    res_down = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "-1"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res_down.returncode != 0, "Downgrade should have aborted on live position"
    output = res_down.stderr + res_down.stdout
    assert "ROLLBACK PRECONDITION VIOLATION" in output
    assert "1 live position(s) exist in 'positions'" in output


def test_offline_sql_generation_for_postgresql():
    """Verify that alembic generates static PostgreSQL SQL without failing on unexecuted scalar calls."""
    env = os.environ.copy()
    env["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@localhost:5432/gold_trading"

    res = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "4b591a2e8c92:head", "--sql"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Offline SQL generation failed: {res.stderr}"
    sql_output = res.stdout

    # Verify that offline commentary is emitted instead of false claims of execution
    assert "PRE-MIGRATION INTEGRITY SCAN" in sql_output
    assert "CREATE TABLE live_accounts" in sql_output
    assert "CREATE TABLE account_equity_high_water_mark" in sql_output
    assert "ALTER TABLE positions ADD COLUMN paper_account_id" in sql_output
    assert "ALTER TABLE positions ADD COLUMN live_account_id" in sql_output
    assert "chk_positions_account_exclusivity" in sql_output
    assert "POST-BACKFILL DATA VALIDATION" in sql_output


def test_offline_sql_downgrade_generation_for_postgresql():
    """Verify that alembic generates static PostgreSQL downgrade SQL without error."""
    env = os.environ.copy()
    env["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres@localhost:5432/gold_trading"

    res = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "head:4b591a2e8c92", "--sql"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Offline downgrade SQL generation failed: {res.stderr}"
    sql_output = res.stdout

    assert "DOWNGRADE PRECONDITION VALIDATION" in sql_output
    assert "ALTER TABLE positions DROP CONSTRAINT chk_positions_account_exclusivity" in sql_output
    assert "DROP TABLE live_accounts" in sql_output
    assert "DROP TABLE account_equity_high_water_mark" in sql_output
