"""Add source_timeframe column to paper_orders.

Records which timeframe resolution produced the entry signal (e.g. "1h", "4h").
Part of the multi-timeframe chain audit trail introduced in Section 3.

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-26 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "paper_orders",
        sa.Column("source_timeframe", sa.String(8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("paper_orders", "source_timeframe")
