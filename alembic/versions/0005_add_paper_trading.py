"""Add paper_positions and paper_orders tables.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-14 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False, unique=True),
        sa.Column("strategy_name", sa.String(256), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("side", sa.String(8), nullable=False, server_default="FLAT"),
        sa.Column("qty", sa.Float(), nullable=False, server_default="0"),
        sa.Column("entry_price", sa.Float(), nullable=True),
        sa.Column("entry_time", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("peak_equity", sa.Float(), nullable=False),
        sa.Column("daily_start_equity", sa.Float(), nullable=False),
        sa.Column("daily_halt", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("account_failed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "last_update",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_pp_run_id", "paper_positions", ["run_id"])

    op.create_table(
        "paper_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("strategy_name", sa.String(256), nullable=False),
        sa.Column("symbol", sa.String(64), nullable=False),
        sa.Column("event", sa.String(16), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("qty", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("pnl", sa.Float(), nullable=True),
        sa.Column("commission", sa.Float(), nullable=False, server_default="0"),
        sa.Column("order_type", sa.String(16), nullable=False, server_default="paper"),
        sa.Column("exchange_order_id", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_po_run_id", "paper_orders", ["run_id"])
    op.create_index("idx_po_created_at", "paper_orders", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_po_created_at", table_name="paper_orders")
    op.drop_index("idx_po_run_id", table_name="paper_orders")
    op.drop_table("paper_orders")
    op.drop_index("idx_pp_run_id", table_name="paper_positions")
    op.drop_table("paper_positions")
