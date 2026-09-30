"""Add Senior Trader (Agent Orchestration) Phase 1 tables: decisions,
incidents, trade_journal.

Additive only — no existing table is altered. See
AGENT_ORCHESTRATION.md and state/integration_plan.md Section 2 for the
design these implement.

`decisions` records every Senior Trader decision, including NO_TRADE, keyed
to the pending_signals row (if any) and gate_decisions row (if any) it was
made about — both nullable because a session-level "no active signals at
all" decision refers to neither.

`incidents` is the escalation log — append-only, resolved_at nullable until
a human closes it out.

`trade_journal` is the manual-execution journal gap identified in
state/repository_map.md Section 6: paper_orders assumes an automated fill,
pending_signals only tracks the proposal, and neither can record what a
human actually did with an approved Kraken Prop setup. Phase 1 creates the
schema; nothing populates it yet (no execution capability exists yet).

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-30 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("decision_id", sa.String(36), nullable=False, unique=True),
        sa.Column("pending_signal_id", sa.Integer(), sa.ForeignKey("pending_signals.id"), nullable=True),
        sa.Column("gate_decision_id", sa.Integer(), sa.ForeignKey("gate_decisions.id"), nullable=True),
        sa.Column("decision", sa.String(16), nullable=False),  # TAKE | NO_TRADE
        sa.Column("no_trade_reason", sa.String(64), nullable=True),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("idx_decisions_pending_signal", "decisions", ["pending_signal_id"])
    op.create_index("idx_decisions_decision", "decisions", ["decision"])

    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(36), nullable=False, unique=True),
        sa.Column("category", sa.String(48), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("current_state", JSONB(), nullable=False),
        sa.Column("observed_state", JSONB(), nullable=False),
        sa.Column("expected_state", JSONB(), nullable=False),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=False),
        sa.Column("execution_status", sa.String(16), nullable=False, server_default="none"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_incidents_category", "incidents", ["category"])
    op.create_index("idx_incidents_unresolved", "incidents", ["resolved_at"])

    op.create_table(
        "trade_journal",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trade_id", sa.String(36), nullable=False, unique=True),
        sa.Column("decision_id", sa.String(36), sa.ForeignKey("decisions.decision_id"), nullable=False),
        sa.Column("pending_signal_id", sa.Integer(), sa.ForeignKey("pending_signals.id"), nullable=True),
        sa.Column("setup_tag", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("signal_price", sa.Numeric(), nullable=False),
        sa.Column("decision_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execution_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fill_price", sa.Numeric(), nullable=True),
        sa.Column("quantity", sa.Numeric(), nullable=True),
        sa.Column("close_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_price", sa.Numeric(), nullable=True),
        sa.Column("close_reason", sa.String(32), nullable=True),
        sa.Column("realized_pnl", sa.Numeric(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
    )
    op.create_index("idx_trade_journal_symbol", "trade_journal", ["symbol"])


def downgrade() -> None:
    op.drop_index("idx_trade_journal_symbol", table_name="trade_journal")
    op.drop_table("trade_journal")

    op.drop_index("idx_incidents_unresolved", table_name="incidents")
    op.drop_index("idx_incidents_category", table_name="incidents")
    op.drop_table("incidents")

    op.drop_index("idx_decisions_decision", table_name="decisions")
    op.drop_index("idx_decisions_pending_signal", table_name="decisions")
    op.drop_table("decisions")
