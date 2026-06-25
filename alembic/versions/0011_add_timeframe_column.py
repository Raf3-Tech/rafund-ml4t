"""Add timeframe column to prices, to support storing multiple timeframes
per symbol (e.g. 1d + 4h + 1h + 15m) instead of just one.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-25 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "prices",
        sa.Column("timeframe", sa.String(8), server_default="1d", nullable=False),
    )

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
        "uq_prices_exchange_symbol_timeframe_timestamp",
        "prices",
        ["exchange", "symbol", "timeframe", "timestamp"],
    )
    op.drop_index("idx_symbol_time", table_name="prices")
    op.create_index("idx_symbol_time", "prices", ["exchange", "symbol", "timeframe", "timestamp"])


def downgrade() -> None:
    op.drop_index("idx_symbol_time", table_name="prices")
    op.create_index("idx_symbol_time", "prices", ["exchange", "symbol", "timestamp"])
    op.drop_constraint("uq_prices_exchange_symbol_timeframe_timestamp", "prices", type_="unique")
    op.create_unique_constraint(
        "uq_prices_exchange_symbol_timestamp", "prices", ["exchange", "symbol", "timestamp"]
    )
    op.drop_column("prices", "timeframe")
