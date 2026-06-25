"""Add stop_price column to paper_positions (structural stop, e.g. SMC
Breakout's order block edge).

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-25 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "paper_positions",
        sa.Column("stop_price", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("paper_positions", "stop_price")
