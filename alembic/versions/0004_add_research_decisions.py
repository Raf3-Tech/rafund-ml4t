"""Add research_decisions table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-09 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("strategy_name", sa.String(256), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=True),
        sa.Column("tier", sa.String(20), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("avg_sharpe", sa.Float(), nullable=False),
        sa.Column("avg_max_dd_pct", sa.Float(), nullable=False),
        sa.Column("avg_win_rate_pct", sa.Float(), nullable=False),
        sa.Column("cons_ratio", sa.Float(), nullable=False),
        sa.Column("std_ratio", sa.Float(), nullable=False),
        sa.Column("perm_ratio", sa.Float(), nullable=False),
        sa.Column("n_windows", sa.Integer(), nullable=False),
        sa.Column("params", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.create_index("idx_rd_strategy", "research_decisions", ["strategy_name"])
    op.create_index("idx_rd_accepted", "research_decisions", ["accepted"])
    op.create_index("idx_rd_created_at", "research_decisions", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_rd_created_at", table_name="research_decisions")
    op.drop_index("idx_rd_accepted", table_name="research_decisions")
    op.drop_index("idx_rd_strategy", table_name="research_decisions")
    op.drop_table("research_decisions")
