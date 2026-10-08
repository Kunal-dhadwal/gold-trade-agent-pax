"""Phase 3: Paper trading accounts, sessions, events, and equity snapshots

Revision ID: 4b591a2e8c92
Revises: 3a290a1f7c81
Create Date: 2026-10-08 17:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4b591a2e8c92'
down_revision: Union[str, Sequence[str], None] = '3a290a1f7c81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema for Phase 3 paper trading entities."""
    # 1. Create paper_accounts table
    op.create_table(
        'paper_accounts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('initial_balance', sa.Float(), nullable=False, server_default='10000.0'),
        sa.Column('current_cash', sa.Float(), nullable=False, server_default='10000.0'),
        sa.Column('peak_equity', sa.Float(), nullable=False, server_default='10000.0'),
        sa.Column('max_drawdown_pct', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('currency', sa.String(length=16), nullable=False, server_default='USDT'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='ACTIVE'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    # 2. Create paper_trading_sessions table
    op.create_table(
        'paper_trading_sessions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('strategy_version_id', sa.String(length=36), nullable=False),
        sa.Column('symbol', sa.String(length=32), nullable=False, server_default='PAXGUSDT'),
        sa.Column('allocated_capital', sa.Float(), nullable=False, server_default='10000.0'),
        sa.Column('status', sa.String(length=24), nullable=False, server_default='PAPER_ACTIVE'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_trades', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('winning_trades', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('losing_trades', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('gross_profit', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('gross_loss', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('realized_pnl', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('max_drawdown_pct', sa.Float(), nullable=False, server_default='0.0'),
        sa.ForeignKeyConstraint(['account_id'], ['paper_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['strategy_version_id'], ['strategy_versions.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_paper_trading_sessions_account_id', 'paper_trading_sessions', ['account_id'])
    op.create_index('ix_paper_trading_sessions_strategy_version_id', 'paper_trading_sessions', ['strategy_version_id'])

    # 3. Create paper_order_events table
    op.create_table(
        'paper_order_events',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('order_id', sa.String(length=36), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('event_type', sa.String(length=32), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('from_state', sa.String(length=24), nullable=False),
        sa.Column('to_state', sa.String(length=24), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['paper_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key', name='uq_paper_order_events_idempotency_key'),
    )
    op.create_index('ix_paper_order_events_order_id', 'paper_order_events', ['order_id'])
    op.create_index('ix_paper_order_events_account_id', 'paper_order_events', ['account_id'])
    op.create_index('ix_paper_order_events_timestamp', 'paper_order_events', ['timestamp'])

    # 4. Create paper_equity_snapshots table
    op.create_table(
        'paper_equity_snapshots',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('equity', sa.Float(), nullable=False),
        sa.Column('cash', sa.Float(), nullable=False),
        sa.Column('used_margin', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('unrealized_pnl', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('drawdown_pct', sa.Float(), nullable=False, server_default='0.0'),
        sa.ForeignKeyConstraint(['account_id'], ['paper_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_paper_equity_snapshots_account_id', 'paper_equity_snapshots', ['account_id'])
    op.create_index('ix_paper_equity_snapshots_timestamp', 'paper_equity_snapshots', ['timestamp'])

    # 5. Add account_id and idempotency_key to paper_trades
    with op.batch_alter_table('paper_trades') as batch_op:
        batch_op.add_column(sa.Column('account_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('idempotency_key', sa.String(length=128), nullable=True))
        batch_op.create_foreign_key('fk_paper_trades_account_id', 'paper_accounts', ['account_id'], ['id'])

    # 6. Add account_id and strategy_version_id to positions
    with op.batch_alter_table('positions') as batch_op:
        batch_op.add_column(sa.Column('account_id', sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column('strategy_version_id', sa.String(length=36), nullable=True))
        batch_op.create_foreign_key('fk_positions_account_id', 'paper_accounts', ['account_id'], ['id'])
        batch_op.create_foreign_key('fk_positions_strategy_version_id', 'strategy_versions', ['strategy_version_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema for Phase 3 paper trading entities."""
    with op.batch_alter_table('positions') as batch_op:
        batch_op.drop_constraint('fk_positions_strategy_version_id', type_='foreignkey')
        batch_op.drop_constraint('fk_positions_account_id', type_='foreignkey')
        batch_op.drop_column('strategy_version_id')
        batch_op.drop_column('account_id')

    with op.batch_alter_table('paper_trades') as batch_op:
        batch_op.drop_constraint('fk_paper_trades_account_id', type_='foreignkey')
        batch_op.drop_column('idempotency_key')
        batch_op.drop_column('account_id')

    op.drop_table('paper_equity_snapshots')
    op.drop_table('paper_order_events')
    op.drop_table('paper_trading_sessions')
    op.drop_table('paper_accounts')

