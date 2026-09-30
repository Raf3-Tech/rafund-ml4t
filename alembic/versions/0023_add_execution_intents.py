"""Add execution_intents table — Agent Orchestration Phase 3 (Paper Trader).

Additive only — no existing table is altered. See AGENT_ORCHESTRATION.md
Section 13/14 and state/integration_plan.md's Phase 3 section for the
design this implements.

An execution_intents row is built only for a TAKE decision
(senior_trader/execution_intent.py::build_execution_intent) and is
immutable except for its `status`/`fill_price`/`filled_at` columns, updated
once senior_trader/simulation.py (Phase 3) — or a later phase's real
execution — resolves it. `status` starts 'pending', becomes 'filled' or
'expired'.

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-30 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "execution_intents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("intent_id", sa.String(36), nullable=False, unique=True),
        sa.Column("decision_id", sa.String(36), sa.ForeignKey("decisions.decision_id"), nullable=False),
        sa.Column("pending_signal_id", sa.Integer(), sa.ForeignKey("pending_signals.id"), nullable=True),
        sa.Column("account", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),         # BUY | SELL
        sa.Column("order_type", sa.String(16), nullable=False),  # always "limit" today
        sa.Column("price", sa.Numeric(), nullable=False),
        sa.Column("quantity", sa.Numeric(), nullable=False),
        sa.Column("reduce_only", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("gate_decision", sa.String(16), nullable=False),  # APPROVE | REDUCE | UNKNOWN
        sa.Column("gate_snapshot", JSONB(), nullable=False),
        sa.Column("setup_tag", sa.String(64), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execution_mode", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("fill_price", sa.Numeric(), nullable=True),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("idx_execution_intents_decision", "execution_intents", ["decision_id"])
    op.create_index("idx_execution_intents_status", "execution_intents", ["status"])


def downgrade() -> None:
    op.drop_index("idx_execution_intents_status", table_name="execution_intents")
    op.drop_index("idx_execution_intents_decision", table_name="execution_intents")
    op.drop_table("execution_intents")
