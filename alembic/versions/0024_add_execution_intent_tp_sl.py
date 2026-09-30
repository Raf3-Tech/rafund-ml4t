"""Add stop_price/take_profit_price to execution_intents — Phase 5 risk
mitigation mandate.

Additive only. Per explicit account-holder direction (2026-09-30): every
order this system populates on the real Kraken Pro app must have TP/SL
attached, at a 1:3 risk:reward ratio (see
signals/pending_signal_detector.py's _DEFAULT_R_MULTIPLE). These two
columns relay the pending_signals row's own already-gate-approved
stop_price/take_profit_price verbatim — see
senior_trader/execution_intent.py::build_execution_intent.

Existing rows (Phase 3 paper-trading intents, built before this mandate)
have no natural stop/target to backfill from, so the new columns are
nullable rather than back-computed — Phase 3's simulator never used them
and does not need to.

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-30 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("execution_intents", sa.Column("stop_price", sa.Numeric(), nullable=True))
    op.add_column("execution_intents", sa.Column("take_profit_price", sa.Numeric(), nullable=True))


def downgrade() -> None:
    op.drop_column("execution_intents", "take_profit_price")
    op.drop_column("execution_intents", "stop_price")
