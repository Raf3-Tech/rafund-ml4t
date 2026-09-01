"""Add equity_curve table — append-only equity snapshots written by
trading/live_loop.py after every strategy-symbol cycle, independent of the
research/retrain cycle's engine_results.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-25 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equity_curve",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(length=100), nullable=False),
        sa.Column("strategy_name", sa.String(length=100), nullable=False),
        sa.Column("symbol", sa.String(length=50), nullable=False),
        sa.Column("exchange", sa.String(length=20), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column(
            "recorded_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "idx_equity_curve_run_id", "equity_curve", ["run_id", "recorded_at"]
    )
    op.create_index(
        "idx_equity_curve_exchange", "equity_curve", ["exchange", "recorded_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_equity_curve_exchange", table_name="equity_curve")
    op.drop_index("idx_equity_curve_run_id", table_name="equity_curve")
    op.drop_table("equity_curve")
