"""Add gate_decisions table — the pre-trade gate's audit log.

Append-only: every risk.pretrade_gate.evaluate() call persists here with its
full inputs (setup, account state, context) and the decision, regardless of
outcome, so the gate is auditable after the fact per the Kraken Prop brief.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-06 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gate_decisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("strategy_name", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),  # APPROVE | REDUCE | REJECT
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("inputs", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("idx_gate_decisions_strategy_symbol", "gate_decisions", ["strategy_name", "symbol"])
    op.create_index("idx_gate_decisions_action", "gate_decisions", ["action"])


def downgrade() -> None:
    op.drop_index("idx_gate_decisions_action", table_name="gate_decisions")
    op.drop_index("idx_gate_decisions_strategy_symbol", table_name="gate_decisions")
    op.drop_table("gate_decisions")
