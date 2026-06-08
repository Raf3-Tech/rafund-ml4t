"""Add engine_results and funding_rates tables.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-07 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'engine_results',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('run_id', UUID(as_uuid=False), nullable=False),
        sa.Column('strategy_name', sa.String(length=100), nullable=False),
        sa.Column('symbol', sa.String(length=50), nullable=False),
        sa.Column('window_type', sa.String(length=20), nullable=False),
        sa.Column('window_start', sa.Date(), nullable=False),
        sa.Column('window_end', sa.Date(), nullable=False),
        sa.Column('window_years', sa.Float(), nullable=False),
        sa.Column('params', JSONB(), nullable=False),
        sa.Column('total_return_pct', sa.Float(), nullable=True),
        sa.Column('sharpe_ratio', sa.Float(), nullable=True),
        sa.Column('max_drawdown_pct', sa.Float(), nullable=True),
        sa.Column('win_rate_pct', sa.Float(), nullable=True),
        sa.Column('num_trades', sa.Integer(), nullable=True),
        sa.Column('conservative_pass', sa.Boolean(), nullable=True),
        sa.Column('standard_pass', sa.Boolean(), nullable=True),
        sa.Column('permissive_pass', sa.Boolean(), nullable=True),
        sa.Column('failure_reason', sa.Text(), nullable=True),
        sa.Column('mutation_generation', sa.Integer(), server_default='0', nullable=False),
        sa.Column('parent_run_id', UUID(as_uuid=False), nullable=True),
        sa.Column('bars_used', sa.Integer(), nullable=True),
        sa.Column('regime_trend', sa.Float(), nullable=True),
        sa.Column('regime_volatility', sa.Float(), nullable=True),
        sa.Column('regime_direction', sa.String(length=10), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('NOW()'), nullable=False),
    )
    op.create_index('idx_engine_results_strategy', 'engine_results', ['strategy_name'])
    op.create_index('idx_engine_results_symbol', 'engine_results', ['symbol'])
    op.create_index('idx_engine_results_run_id', 'engine_results', ['run_id'])
    op.create_index('idx_engine_results_passes', 'engine_results', ['conservative_pass', 'standard_pass', 'permissive_pass'])

    op.create_table(
        'funding_rates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('symbol', sa.String(length=50), nullable=False),
        sa.Column('funding_time', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('funding_rate', sa.Float(), nullable=False),
        sa.Column('mark_price', sa.Float(), nullable=True),
        sa.UniqueConstraint('symbol', 'funding_time', name='uq_funding_symbol_time'),
    )
    op.create_index('idx_funding_rates_symbol', 'funding_rates', ['symbol'])
    op.create_index('idx_funding_rates_time', 'funding_rates', ['funding_time'])


def downgrade() -> None:
    op.drop_index('idx_funding_rates_time', table_name='funding_rates')
    op.drop_index('idx_funding_rates_symbol', table_name='funding_rates')
    op.drop_table('funding_rates')

    op.drop_index('idx_engine_results_passes', table_name='engine_results')
    op.drop_index('idx_engine_results_run_id', table_name='engine_results')
    op.drop_index('idx_engine_results_symbol', table_name='engine_results')
    op.drop_index('idx_engine_results_strategy', table_name='engine_results')
    op.drop_table('engine_results')
