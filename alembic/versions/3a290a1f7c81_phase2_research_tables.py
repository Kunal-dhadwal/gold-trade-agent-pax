"""Phase 2: Research sessions, hypotheses, and strategy version linkage

Revision ID: 3a290a1f7c81
Revises: cdbcf9b8dc53
Create Date: 2026-10-08 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3a290a1f7c81'
down_revision: Union[str, Sequence[str], None] = 'cdbcf9b8dc53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema for Phase 2 research entities."""
    # 1. Create research_sessions table
    op.create_table(
        'research_sessions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='ACTIVE'),
        sa.Column('target_regime', sa.String(length=32), nullable=False, server_default='ALL'),
        sa.Column('timeframe', sa.String(length=16), nullable=False, server_default='1h'),
        sa.Column('instrument', sa.String(length=32), nullable=False, server_default='XAUUSD'),
        sa.Column('iterations_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_iterations', sa.Integer(), nullable=False, server_default='10'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    # 2. Create research_hypotheses table
    op.create_table(
        'research_hypotheses',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('session_id', sa.String(length=36), nullable=False),
        sa.Column('thesis_statement', sa.Text(), nullable=False),
        sa.Column('economic_rationale', sa.Text(), nullable=False),
        sa.Column('expected_edge', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='FORMULATED'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['research_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # 3. Batch alter strategy_versions for SQLite parity (P3-2 Resolution)
    with op.batch_alter_table('strategy_versions') as batch_op:
        batch_op.add_column(sa.Column('hypothesis_id', sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            'fk_strategy_versions_hypothesis_id',
            'research_hypotheses',
            ['hypothesis_id'],
            ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('strategy_versions') as batch_op:
        batch_op.drop_constraint('fk_strategy_versions_hypothesis_id', type_='foreignkey')
        batch_op.drop_column('hypothesis_id')

    op.drop_table('research_hypotheses')
    op.drop_table('research_sessions')

