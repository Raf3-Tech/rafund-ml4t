"""Convert pending_signals price/size columns to NUMERIC + add Kraken Prop
risk/cost columns (Phase 3 of the Kraken Prop risk gate).

A Decimal value written into a FLOAT8 column is silently downgraded to
binary-float precision on write — exactly the failure mode "Use Decimal
throughout, no floats in the risk/cost path" (AGENTS.md, KRAKEN PROP GAP
BACKLOG) is meant to prevent. The table is empty in every environment this
has run in so far, so this is a plain type change, not a backfill.

leaderboard_score stays FLOAT — a ranking display metric, not part of the
risk/cost path.

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-06 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_NULLABLE_PRICE_COLUMNS = ("stop_price", "take_profit_price", "position_size_usd", "qty")


def upgrade() -> None:
    op.alter_column("pending_signals", "limit_price", type_=sa.Numeric(), existing_nullable=False)
    for col in _NULLABLE_PRICE_COLUMNS:
        op.alter_column("pending_signals", col, type_=sa.Numeric(), existing_nullable=True)

    op.add_column("pending_signals", sa.Column("expected_cost", sa.Numeric(), nullable=True))
    op.add_column("pending_signals", sa.Column("worst_case_loss", sa.Numeric(), nullable=True))
    op.add_column("pending_signals", sa.Column("r_multiple", sa.Numeric(), nullable=True))
    op.add_column("pending_signals", sa.Column("leverage_required", sa.Numeric(), nullable=True))
    op.add_column("pending_signals", sa.Column("expected_hold_hours", sa.Numeric(), nullable=True))


def downgrade() -> None:
    op.drop_column("pending_signals", "expected_hold_hours")
    op.drop_column("pending_signals", "leverage_required")
    op.drop_column("pending_signals", "r_multiple")
    op.drop_column("pending_signals", "worst_case_loss")
    op.drop_column("pending_signals", "expected_cost")

    for col in _NULLABLE_PRICE_COLUMNS:
        op.alter_column("pending_signals", col, type_=sa.Float(), existing_nullable=True)
    op.alter_column("pending_signals", "limit_price", type_=sa.Float(), existing_nullable=False)
