"""Add setup_tag and close_reason columns to paper_orders (trade journal).

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-20 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_orders", sa.Column("setup_tag", sa.String(64), nullable=True))
    op.add_column("paper_orders", sa.Column("close_reason", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("paper_orders", "close_reason")
    op.drop_column("paper_orders", "setup_tag")
