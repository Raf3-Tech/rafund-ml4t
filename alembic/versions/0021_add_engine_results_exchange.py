"""Add exchange provenance columns to engine_results.

backtesting/window_engine.py previously called db.get_prices(symbol, None,
None) with no exchange filter, so a backtest could silently blend rows from
multiple exchanges for the same symbol string, and engine_results never
recorded which exchange (if any) sourced a given result. Fixed going
forward in this same change (window_engine.py now resolves and requires a
single, established source exchange via data.provenance before running,
raising rather than blending on an ambiguous symbol).

exchange_provenance distinguishes what kind of claim `exchange` is:
  - 'verified'             — the engine explicitly resolved and confirmed a
                              single source exchange before this row was produced.
  - 'inferred_from_symbol' — backfilled after the fact from current
                              instrument_provenance for a legacy row, because
                              that symbol happens to be single-sourced today.
                              Not a per-row fact recorded at insert time.
  - 'unknown'              — cannot be established (legacy row for a symbol
                              whose prices come from more than one exchange,
                              or for which no provenance exists at all). Not
                              backfilled with a guess.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-06 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("engine_results", sa.Column("exchange", sa.String(16), nullable=True))
    op.add_column(
        "engine_results",
        sa.Column("exchange_provenance", sa.String(24), nullable=False, server_default="unknown"),
    )
    op.create_index("idx_engine_results_exchange", "engine_results", ["exchange"])


def downgrade() -> None:
    op.drop_index("idx_engine_results_exchange", table_name="engine_results")
    op.drop_column("engine_results", "exchange_provenance")
    op.drop_column("engine_results", "exchange")
