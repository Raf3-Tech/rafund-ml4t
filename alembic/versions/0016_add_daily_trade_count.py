"""Add daily_trade_count column to paper_positions.

Persists the intraday trade count across process restarts so max_trades_per_day
is enforced correctly when the loop re-invokes python main.py paper within the
same calendar day.

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-30 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "paper_positions",
        sa.Column("daily_trade_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("paper_positions", "daily_trade_count")
