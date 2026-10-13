"""Phase 4 Milestone 4.2: Live execution schemas, account-aware positions, fee schedules, and high-water mark

Revision ID: 5c6a7b8e9f01
Revises: 4b591a2e8c92
Create Date: 2026-10-09 16:40:00.000000

"""
from typing import Sequence, Union
from alembic import op, context
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c6a7b8e9f01'
down_revision: Union[str, Sequence[str], None] = '4b591a2e8c92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema for Phase 4 Milestone 4.2 live execution architecture."""
    bind = op.get_bind()

    # -------------------------------------------------------------------------
    # Step 1: Pre-Migration Data Integrity & Orphan Detection Scan
    # -------------------------------------------------------------------------
    if context.is_offline_mode():
        op.execute(
            "-- PRE-MIGRATION INTEGRITY SCAN: In online execution, verifies zero orphaned or invalid "
            "-- positions exist before DDL application. (Requires active database connection)."
        )
    else:
        orphan_query = sa.text("""
            SELECT count(*) FROM positions 
            WHERE (account_id IS NOT NULL AND account_id NOT IN (SELECT id FROM paper_accounts))
               OR (account_id IS NULL AND quantity != 0.0)
        """)
        orphan_count = bind.execute(orphan_query).scalar() or 0
        if orphan_count > 0:
            raise RuntimeError(
                f"PRE-MIGRATION INTEGRITY ERROR: Detected {orphan_count} orphaned or invalid "
                "position record(s) in 'positions' referencing non-existent accounts. "
                "Aborting migration transaction without modifying schema."
            )

    # -------------------------------------------------------------------------
    # Step 2: Create Live Execution Tables
    # -------------------------------------------------------------------------

    # 1. live_accounts
    op.create_table(
        'live_accounts',
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('currency', sa.String(length=16), server_default='USDT', nullable=False),
        sa.Column('initial_balance', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('current_cash', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('status', sa.String(length=16), server_default='ACTIVE', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('account_id'),
    )

    # 2. account_fee_schedules
    op.create_table(
        'account_fee_schedules',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('product_symbol', sa.String(length=32), server_default='PAXGUSDT', nullable=False),
        sa.Column('tier_level', sa.Integer(), server_default='1', nullable=False),
        sa.Column('maker_rate', sa.Float(), server_default='0.0005', nullable=False),
        sa.Column('taker_rate', sa.Float(), server_default='0.0010', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column('source', sa.String(length=32), server_default='REST_AUTHENTICATED_TIER_1', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['live_accounts.account_id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_account_fee_schedules_account_id', 'account_fee_schedules', ['account_id'])
    op.create_index('ix_account_fee_schedules_account_symbol', 'account_fee_schedules', ['account_id', 'product_symbol', 'is_active'])

    # 3. live_orders
    op.create_table(
        'live_orders',
        sa.Column('order_id', sa.String(length=36), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('client_order_id', sa.String(length=36), nullable=False),
        sa.Column('exchange_order_id', sa.String(length=64), nullable=True),
        sa.Column('product_symbol', sa.String(length=32), server_default='PAXGUSDT', nullable=False),
        sa.Column('side', sa.String(length=10), nullable=False),
        sa.Column('order_type', sa.String(length=20), nullable=False),
        sa.Column('time_in_force', sa.String(length=10), server_default='GTC', nullable=False),
        sa.Column('state', sa.String(length=24), server_default='CREATED', nullable=False),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('price', sa.Float(), nullable=True),
        sa.Column('stop_price', sa.Float(), nullable=True),
        sa.Column('filled_quantity', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('average_fill_price', sa.Float(), nullable=True),
        sa.Column('reduce_only', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['live_accounts.account_id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('order_id'),
    )
    op.create_index('ix_live_orders_account_id', 'live_orders', ['account_id'])
    op.create_index('ix_live_orders_client_order_id', 'live_orders', ['client_order_id'], unique=True)
    op.create_index('ix_live_orders_exchange_order_id', 'live_orders', ['exchange_order_id'])
    op.create_index('ix_live_orders_state', 'live_orders', ['state'])
    op.create_index('ix_live_orders_account_state', 'live_orders', ['account_id', 'state'])
    op.create_index('ix_live_orders_created_at', 'live_orders', ['created_at'])

    # 4. live_order_events
    op.create_table(
        'live_order_events',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('order_id', sa.String(length=36), nullable=False),
        sa.Column('from_state', sa.String(length=24), nullable=False),
        sa.Column('to_state', sa.String(length=24), nullable=False),
        sa.Column('event_type', sa.String(length=32), nullable=False),
        sa.Column('payload', sa.Text(), nullable=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['live_orders.order_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_live_order_events_order_id', 'live_order_events', ['order_id'])
    op.create_index('ix_live_order_events_timestamp', 'live_order_events', ['timestamp'])

    # 5. leader_leases
    op.create_table(
        'leader_leases',
        sa.Column('resource_name', sa.String(length=64), nullable=False),
        sa.Column('owner_id', sa.String(length=64), nullable=False),
        sa.Column('lease_epoch', sa.BigInteger().with_variant(sa.Integer(), "sqlite"), server_default='1', nullable=False),
        sa.Column('acquired_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('resource_name'),
    )
    op.create_index('ix_leader_leases_expires_at', 'leader_leases', ['expires_at'])

    # 6. reconciliation_snapshots
    op.create_table(
        'reconciliation_snapshots',
        sa.Column('snapshot_id', sa.String(length=36), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('discrepancy_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('cash_local', sa.Float(), nullable=False),
        sa.Column('cash_exchange', sa.Float(), nullable=False),
        sa.Column('upnl_local', sa.Float(), nullable=False),
        sa.Column('upnl_exchange', sa.Float(), nullable=False),
        sa.Column('status', sa.String(length=24), server_default='CLEAN', nullable=False),
        sa.Column('details', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['live_accounts.account_id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('snapshot_id'),
    )
    op.create_index('ix_reconciliation_snapshots_account_id', 'reconciliation_snapshots', ['account_id'])
    op.create_index('ix_reconciliation_snapshots_timestamp', 'reconciliation_snapshots', ['timestamp'])
    op.create_index('ix_reconciliation_snapshots_account_time', 'reconciliation_snapshots', ['account_id', 'timestamp'])

    # 7. live_funding_events
    op.create_table(
        'live_funding_events',
        sa.Column('event_id', sa.String(length=36), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('product_symbol', sa.String(length=32), server_default='PAXGUSDT', nullable=False),
        sa.Column('funding_rate', sa.Float(), nullable=False),
        sa.Column('payment_amount', sa.Float(), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['live_accounts.account_id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('event_id'),
    )
    op.create_index('ix_live_funding_events_account_id', 'live_funding_events', ['account_id'])
    op.create_index('ix_live_funding_events_timestamp', 'live_funding_events', ['timestamp'])
    op.create_index('ix_live_funding_events_account_symbol_time', 'live_funding_events', ['account_id', 'product_symbol', 'timestamp'])

    # 8. operator_reconciliation_audit_logs
    op.create_table(
        'operator_reconciliation_audit_logs',
        sa.Column('log_id', sa.String(length=36), nullable=False),
        sa.Column('snapshot_id', sa.String(length=36), nullable=True),
        sa.Column('action_type', sa.String(length=32), nullable=False),
        sa.Column('operator_id', sa.String(length=64), nullable=False),
        sa.Column('parameters', sa.Text(), nullable=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['snapshot_id'], ['reconciliation_snapshots.snapshot_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('log_id'),
    )
    op.create_index('ix_operator_audit_logs_timestamp', 'operator_reconciliation_audit_logs', ['timestamp'])

    # 9. consumed_authorization_nonces
    op.create_table(
        'consumed_authorization_nonces',
        sa.Column('nonce', sa.String(length=64), nullable=False),
        sa.Column('signer_id', sa.String(length=64), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('nonce'),
    )
    op.create_index('ix_consumed_nonces_timestamp', 'consumed_authorization_nonces', ['consumed_at'])

    # 10. account_equity_high_water_mark
    op.create_table(
        'account_equity_high_water_mark',
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('peak_equity', sa.Float(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fencing_epoch', sa.BigInteger().with_variant(sa.Integer(), "sqlite"), server_default='1', nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['live_accounts.account_id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('account_id'),
    )

    # -------------------------------------------------------------------------
    # Step 3: Expand 'positions' Schema with Account-Type-Aware Columns
    # -------------------------------------------------------------------------
    with op.batch_alter_table('positions') as batch_op:
        batch_op.add_column(sa.Column('paper_account_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('live_account_id', sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            'fk_positions_paper_account_id', 'paper_accounts', ['paper_account_id'], ['id'], ondelete='RESTRICT'
        )
        batch_op.create_foreign_key(
            'fk_positions_live_account_id', 'live_accounts', ['live_account_id'], ['account_id'], ondelete='RESTRICT'
        )

    # -------------------------------------------------------------------------
    # Step 4: Deterministic Legacy Data Backfill
    # -------------------------------------------------------------------------
    op.execute(sa.text("""
        UPDATE positions 
        SET paper_account_id = account_id,
            mode = 'PAPER'
        WHERE mode = 'PAPER' OR mode IS NULL;
    """))

    # -------------------------------------------------------------------------
    # Step 5: Post-Backfill Data Validation
    # -------------------------------------------------------------------------
    if context.is_offline_mode():
        op.execute(
            "-- POST-BACKFILL DATA VALIDATION: In online execution, verifies 100% of rows have "
            "-- valid account mappings before applying exclusivity constraints. (Requires active database connection)."
        )
    else:
        unmapped_paper = bind.execute(sa.text("""
            SELECT count(*) FROM positions WHERE mode = 'PAPER' AND paper_account_id IS NULL;
        """)).scalar() or 0
        if unmapped_paper > 0:
            raise RuntimeError(
                f"POST-BACKFILL VALIDATION ERROR: {unmapped_paper} row(s) in 'positions' "
                "failed to map to 'paper_account_id'. Rolling back migration."
            )

        invalid_dual_refs = bind.execute(sa.text("""
            SELECT count(*) FROM positions WHERE paper_account_id IS NOT NULL AND live_account_id IS NOT NULL;
        """)).scalar() or 0
        if invalid_dual_refs > 0:
            raise RuntimeError(
                f"POST-BACKFILL VALIDATION ERROR: {invalid_dual_refs} row(s) have both "
                "'paper_account_id' and 'live_account_id' set. Rolling back migration."
            )

    # -------------------------------------------------------------------------
    # Step 6: Apply Enforceable XOR Check Constraint and Drop Legacy FK
    # -------------------------------------------------------------------------
    with op.batch_alter_table('positions') as batch_op:
        if bind.dialect.name != "sqlite":
            batch_op.drop_constraint('fk_positions_account_id', type_='foreignkey')
        batch_op.create_check_constraint(
            'chk_positions_account_exclusivity',
            "((mode = 'PAPER' AND paper_account_id IS NOT NULL AND live_account_id IS NULL) OR "
            "(mode = 'LIVE' AND live_account_id IS NOT NULL AND paper_account_id IS NULL))"
        )

    op.create_index('ix_positions_live_account_id', 'positions', ['live_account_id'])


def downgrade() -> None:
    """Safely downgrade schema with executable precondition checks."""
    bind = op.get_bind()

    if context.is_offline_mode():
        op.execute(
            "-- DOWNGRADE PRECONDITION VALIDATION: In online execution, verifies that zero live positions "
            "-- and zero records exist in Milestone 4.2 tables before allowing schema rollback. (Requires active database connection)."
        )
    else:
        # -------------------------------------------------------------------------
        # Downgrade Precondition Check 1: No Live Positions Exist
        # -------------------------------------------------------------------------
        try:
            live_pos_count = bind.execute(sa.text(
                "SELECT count(*) FROM positions WHERE live_account_id IS NOT NULL;"
            )).scalar() or 0
            if live_pos_count > 0:
                raise RuntimeError(
                    f"ROLLBACK PRECONDITION VIOLATION: Cannot downgrade schema: {live_pos_count} live "
                    "position(s) exist in 'positions'. Destructive downgrade prohibited to prevent data loss."
                )
        except Exception as e:
            if "ROLLBACK PRECONDITION VIOLATION" in str(e):
                raise

        # -------------------------------------------------------------------------
        # Downgrade Precondition Check 2: All 10 Milestone 4.2 Tables Must Be Empty
        # -------------------------------------------------------------------------
        m42_tables = [
            'account_equity_high_water_mark',
            'operator_reconciliation_audit_logs',
            'live_funding_events',
            'reconciliation_snapshots',
            'leader_leases',
            'live_order_events',
            'live_orders',
            'account_fee_schedules',
            'consumed_authorization_nonces',
            'live_accounts',
        ]

        for tbl in m42_tables:
            try:
                row_count = bind.execute(sa.text(f"SELECT count(*) FROM {tbl};")).scalar() or 0
                if row_count > 0:
                    raise RuntimeError(
                        f"ROLLBACK PRECONDITION VIOLATION: Cannot downgrade schema: table '{tbl}' "
                        f"contains {row_count} persistent row(s). Destructive downgrade prohibited "
                        "to preserve immutable financial audit records."
                    )
            except Exception as e:
                if "ROLLBACK PRECONDITION VIOLATION" in str(e):
                    raise

    # -------------------------------------------------------------------------
    # Revert 'positions' Table Schema Changes
    # -------------------------------------------------------------------------
    try:
        op.drop_index('ix_positions_live_account_id', table_name='positions')
    except Exception:
        pass

    with op.batch_alter_table('positions') as batch_op:
        batch_op.drop_constraint('chk_positions_account_exclusivity', type_='check')
        if bind.dialect.name != "sqlite":
            batch_op.create_foreign_key(
                'fk_positions_account_id', 'paper_accounts', ['account_id'], ['id']
            )
            batch_op.drop_constraint('fk_positions_live_account_id', type_='foreignkey')
            batch_op.drop_constraint('fk_positions_paper_account_id', type_='foreignkey')
        batch_op.drop_column('live_account_id')
        batch_op.drop_column('paper_account_id')

    # -------------------------------------------------------------------------
    # Drop All 10 Live Tables in Reverse Dependency Order
    # -------------------------------------------------------------------------
    op.drop_table('account_equity_high_water_mark')
    op.drop_table('operator_reconciliation_audit_logs')
    op.drop_table('live_funding_events')
    op.drop_table('reconciliation_snapshots')
    op.drop_table('leader_leases')
    op.drop_table('live_order_events')
    op.drop_table('live_orders')
    op.drop_table('account_fee_schedules')
    op.drop_table('consumed_authorization_nonces')
    op.drop_table('live_accounts')
