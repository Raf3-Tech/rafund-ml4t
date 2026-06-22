"""Add exchange column to prices, paper_positions, paper_orders.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-20 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "prices",
        sa.Column("exchange", sa.String(16), server_default="binance", nullable=False),
    )

    # The unique constraint on (symbol, timestamp) was created with different
    # auto-generated names depending on how the DB was originally bootstrapped
    # (raw schema.sql vs. this migration chain) — look it up by columns rather
    # than assuming a fixed name.
    bind = op.get_bind()
    existing = bind.execute(
        sa.text(
            """
            SELECT con.conname
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            WHERE rel.relname = 'prices' AND con.contype = 'u'
            """
        )
    ).scalar()
    if existing:
        op.drop_constraint(existing, "prices", type_="unique")
    op.create_unique_constraint(
        "uq_prices_exchange_symbol_timestamp", "prices", ["exchange", "symbol", "timestamp"]
    )
    op.drop_index("idx_symbol_time", table_name="prices")
    op.create_index("idx_symbol_time", "prices", ["exchange", "symbol", "timestamp"])

    op.add_column(
        "paper_positions",
        sa.Column("exchange", sa.String(16), server_default="binance", nullable=False),
    )
    op.add_column(
        "paper_orders",
        sa.Column("exchange", sa.String(16), server_default="binance", nullable=False),
    )
    op.create_index("idx_pp_run_id_exchange", "paper_positions", ["run_id", "exchange"])
    op.create_index("idx_po_run_id_exchange", "paper_orders", ["run_id", "exchange"])


def downgrade() -> None:
    op.drop_index("idx_po_run_id_exchange", table_name="paper_orders")
    op.drop_index("idx_pp_run_id_exchange", table_name="paper_positions")
    op.drop_column("paper_orders", "exchange")
    op.drop_column("paper_positions", "exchange")

    op.drop_index("idx_symbol_time", table_name="prices")
    op.create_index("idx_symbol_time", "prices", ["symbol", "timestamp"])
    op.drop_constraint("uq_prices_exchange_symbol_timestamp", "prices", type_="unique")
    op.create_unique_constraint(
        "uq_prices_symbol_timestamp", "prices", ["symbol", "timestamp"]
    )
    op.drop_column("prices", "exchange")
