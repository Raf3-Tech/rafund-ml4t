"""Add drift_reports, model_registry, and model_blocks tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'drift_reports',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('model_name', sa.String(length=255), nullable=False),
        sa.Column('symbol', sa.String(length=50), nullable=False),
        sa.Column('detected_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('features_checked', sa.Integer(), nullable=True),
        sa.Column('features_drifted', sa.JSON(), nullable=True),
        sa.Column('max_psi', sa.Float(), nullable=True),
        sa.Column('mean_psi', sa.Float(), nullable=True),
        sa.Column('drift_detected', sa.Boolean(), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=True),
        sa.Column('recommended_action', sa.String(length=50), nullable=True),
        sa.Column('raw_psi_scores', sa.JSON(), nullable=True),
    )
    op.create_index('idx_drift_reports_model_symbol_detected', 'drift_reports', ['model_name', 'symbol', 'detected_at'])

    op.create_table(
        'model_registry',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('model_name', sa.String(length=255), nullable=False),
        sa.Column('mlflow_run_id', sa.String(length=255), nullable=True),
        sa.Column('mlflow_version', sa.Integer(), nullable=True),
        sa.Column('symbol', sa.String(length=50), nullable=True),
        sa.Column('stage', sa.String(length=50), nullable=True),
        sa.Column('trained_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('promoted_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('in_sample_sharpe', sa.Float(), nullable=True),
        sa.Column('oos_sharpe', sa.Float(), nullable=True),
        sa.Column('feature_names', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('FALSE')),
    )
    op.create_index('idx_model_registry_active', 'model_registry', ['model_name', 'symbol', 'is_active'])
    op.create_index(
        'uq_model_registry_active_true',
        'model_registry',
        ['model_name', 'symbol'],
        unique=True,
        postgresql_where=sa.text('is_active = TRUE'),
    )

    op.create_table(
        'model_blocks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('model_name', sa.String(length=255), nullable=False),
        sa.Column('symbol', sa.String(length=50), nullable=False),
        sa.Column('reason', sa.String(length=50), nullable=False),
        sa.Column('blocked_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('NOW()')),
    )
    op.create_index('idx_model_blocks_model_symbol', 'model_blocks', ['model_name', 'symbol'])


def downgrade() -> None:
    op.drop_index('idx_model_blocks_model_symbol', table_name='model_blocks')
    op.drop_table('model_blocks')

    op.drop_index('uq_model_registry_active_true', table_name='model_registry')
    op.drop_index('idx_model_registry_active', table_name='model_registry')
    op.drop_table('model_registry')

    op.drop_index('idx_drift_reports_model_symbol_detected', table_name='drift_reports')
    op.drop_table('drift_reports')
