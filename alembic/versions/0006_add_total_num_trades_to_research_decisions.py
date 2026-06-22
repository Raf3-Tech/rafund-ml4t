"""Add total_num_trades column to research_decisions.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-20 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_decisions",
        sa.Column("total_num_trades", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("research_decisions", "total_num_trades")
