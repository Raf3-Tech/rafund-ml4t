"""Collapse research_decisions tier columns into a single pass_ratio.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-20 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "research_decisions",
        sa.Column("pass_ratio", sa.Float(), server_default="0", nullable=False),
    )
    op.execute("UPDATE research_decisions SET pass_ratio = perm_ratio")
    op.drop_column("research_decisions", "tier")
    op.drop_column("research_decisions", "cons_ratio")
    op.drop_column("research_decisions", "std_ratio")
    op.drop_column("research_decisions", "perm_ratio")


def downgrade() -> None:
    op.add_column("research_decisions", sa.Column("perm_ratio", sa.Float(), server_default="0", nullable=False))
    op.add_column("research_decisions", sa.Column("std_ratio", sa.Float(), server_default="0", nullable=False))
    op.add_column("research_decisions", sa.Column("cons_ratio", sa.Float(), server_default="0", nullable=False))
    op.add_column("research_decisions", sa.Column("tier", sa.String(20), server_default="NONE", nullable=False))
    op.execute("UPDATE research_decisions SET perm_ratio = pass_ratio")
    op.drop_column("research_decisions", "pass_ratio")
