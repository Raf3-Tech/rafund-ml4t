"""Add instrument_provenance table — per-symbol data-source tracking.

Kraken Prop trades manually against Kraken's own price feed. `prices` rows
already carry an `exchange` column, but nothing at the per-instrument level
previously recorded "this symbol's backtest history came from exchange X" —
window_engine.py's db.get_prices(symbol, None, None) pulls ALL exchanges'
rows for a symbol with no exchange filter, so a symbol like BTC/USDT can
silently mix Binance and HTX bars into one series, and engine_results
itself has no exchange column at all. This table is the fix: one row per
symbol, populated by data/provenance.py's audit (not hand-maintained).

prop_verified is separate and narrower: Kraken-sourced is necessary but not
sufficient for the Kraken Prop *product* (a subset of Kraken's spot
universe as leveraged margin contracts). Defaults false; the user populates
it by hand from the Prop market selector. Prop provenance
(is_kraken_sourced) gates research/qualification; prop_verified gates
live-ready status only.

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-06 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instrument_provenance",
        sa.Column("symbol", sa.String(32), primary_key=True),
        # NULL when the symbol's prices rows come from more than one
        # exchange (provenance ambiguous, not just "not Kraken").
        sa.Column("source_exchange", sa.String(16), nullable=True),
        sa.Column("is_kraken_sourced", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("prop_verified", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("last_audited_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("instrument_provenance")
