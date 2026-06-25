"""Add manual_halt column to paper_positions (dashboard kill switch).

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-23 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "paper_positions",
        sa.Column("manual_halt", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("paper_positions", "manual_halt")
