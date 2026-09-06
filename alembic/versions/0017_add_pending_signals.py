"""Add pending_signals table (manual-execution signal layer).

Read-only signal detector proposes limit-order setups for a human to place by
hand; this table is the append-only record of every proposal (whether or not
it's acted on), independent of paper_orders (which records actual fills). The
partial unique index enforces "one active signal per (strategy, symbol,
direction)" idempotency: a re-poll that finds the same setup still active
updates expires_at via ON CONFLICT rather than inserting a duplicate.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-05 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_signals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("strategy_name", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("exchange", sa.String(16), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),  # LONG | SHORT
        sa.Column("limit_price", sa.Float(), nullable=False),
        sa.Column("stop_price", sa.Float(), nullable=True),
        sa.Column("take_profit_price", sa.Float(), nullable=True),
        sa.Column("position_size_usd", sa.Float(), nullable=True),
        sa.Column("qty", sa.Float(), nullable=True),
        sa.Column("leaderboard_score", sa.Float(), nullable=True),
        sa.Column("leaderboard_rank", sa.Integer(), nullable=True),
        sa.Column("thesis", sa.Text(), nullable=True),
        sa.Column("source_timeframe", sa.String(8), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_pending_signals_status", "pending_signals", ["exchange", "status"]
    )
    op.create_index(
        "uq_pending_signals_active",
        "pending_signals",
        ["strategy_name", "symbol", "direction"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_pending_signals_active", table_name="pending_signals")
    op.drop_index("idx_pending_signals_status", table_name="pending_signals")
    op.drop_table("pending_signals")
