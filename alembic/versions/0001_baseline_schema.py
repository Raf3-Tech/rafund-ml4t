"""Baseline database schema migration.

Revision ID: 0001
Revises: None
Create Date: 2026-05-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

signal_col_type = sa.VARCHAR(10)


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN CREATE TYPE signal_type AS ENUM "
        "('BUY', 'SELL', 'HOLD', 'CLOSE'); "
        "EXCEPTION WHEN duplicate_object THEN null; END $$"
    )

    op.create_table(
        'prices',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('open', sa.Float(), nullable=False),
        sa.Column('high', sa.Float(), nullable=False),
        sa.Column('low', sa.Float(), nullable=False),
        sa.Column('close', sa.Float(), nullable=False),
        sa.Column('volume', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('symbol', 'timestamp', name='uq_prices_symbol_timestamp'),
    )
    op.create_index('idx_symbol_time', 'prices', ['symbol', 'timestamp'])
    op.create_index('idx_timestamp', 'prices', ['timestamp'])

    op.create_table(
        'features',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('symbol_a', sa.String(length=20), nullable=False),
        sa.Column('symbol_b', sa.String(length=20), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('spread', sa.Float(), nullable=True),
        sa.Column('spread_mean', sa.Float(), nullable=True),
        sa.Column('spread_std', sa.Float(), nullable=True),
        sa.Column('z_score', sa.Float(), nullable=True),
        sa.Column('hedge_ratio', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('symbol_a', 'symbol_b', 'timestamp', name='uq_features_pair_timestamp'),
    )
    op.create_index('idx_pair_time', 'features', ['symbol_a', 'symbol_b', 'timestamp'])

    op.create_table(
        'signals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('symbol_a', sa.String(length=20), nullable=False),
        sa.Column('symbol_b', sa.String(length=20), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('signal', signal_col_type, nullable=False),
        sa.Column('z_score', sa.Float(), nullable=True),
        sa.Column('position_a', sa.Integer(), nullable=True),
        sa.Column('position_b', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_signal_time', 'signals', ['symbol_a', 'symbol_b', 'timestamp'])

    op.create_table(
        'trades',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('trade_date', sa.DateTime(), nullable=False),
        sa.Column('entry_price', sa.Float(), nullable=False),
        sa.Column('exit_price', sa.Float(), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('pnl', sa.Float(), nullable=True),
        sa.Column('return_pct', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('closed_at', sa.DateTime(), nullable=True),
    )
    op.create_index('idx_trades_symbol', 'trades', ['symbol'])
    op.create_index('idx_trades_status', 'trades', ['status'])

    op.create_table(
        'portfolio',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('position_size', sa.Integer(), nullable=False),
        sa.Column('entry_price', sa.Float(), nullable=False),
        sa.Column('current_price', sa.Float(), nullable=True),
        sa.Column('unrealized_pnl', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('timestamp', 'symbol', name='uq_portfolio_timestamp_symbol'),
    )
    op.create_index('idx_portfolio_time', 'portfolio', ['timestamp'])

    op.create_table(
        'backtest_results',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('backtest_id', sa.String(length=50), nullable=False, unique=True),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('initial_capital', sa.Float(), nullable=False),
        sa.Column('final_value', sa.Float(), nullable=False),
        sa.Column('total_return', sa.Float(), nullable=True),
        sa.Column('sharpe_ratio', sa.Float(), nullable=True),
        sa.Column('max_drawdown', sa.Float(), nullable=True),
        sa.Column('win_rate', sa.Float(), nullable=True),
        sa.Column('num_trades', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('idx_backtest_id', 'backtest_results', ['backtest_id'])


def downgrade() -> None:
    op.drop_index('idx_backtest_id', table_name='backtest_results')
    op.drop_table('backtest_results')

    op.drop_index('idx_portfolio_time', table_name='portfolio')
    op.drop_table('portfolio')

    op.drop_index('idx_trades_status', table_name='trades')
    op.drop_index('idx_trades_symbol', table_name='trades')
    op.drop_table('trades')

    op.drop_index('idx_signal_time', table_name='signals')
    op.drop_table('signals')

    op.drop_index('idx_pair_time', table_name='features')
    op.drop_table('features')

    op.drop_index('idx_symbol_time', table_name='prices')
    op.drop_index('idx_timestamp', table_name='prices')
    op.drop_table('prices')

    op.execute("DROP TYPE IF EXISTS signal_type")
