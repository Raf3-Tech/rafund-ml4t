"""Add system_heartbeat table — one upserted row per long-running process
(e.g. process='live_loop') so an external monitor can detect a stalled or
crashed loop instead of inferring health from log files.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-25 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_heartbeat",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("process", sa.String(length=50), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="running"
        ),
        sa.Column(
            "last_beat", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.UniqueConstraint("process", name="uq_system_heartbeat_process"),
    )


def downgrade() -> None:
    op.drop_table("system_heartbeat")
