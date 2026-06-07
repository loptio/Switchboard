"""initial Run / Output / Schedule tables

Revision ID: 0001
Revises:
Create Date: Phase 2, Unit 1 (DB foundation)

Mirrors db/models.py. Keep the two in sync; future schema changes are new
migrations. Rendered for PostgreSQL (JSONB, native UUID, TIMESTAMPTZ); the
offline test suite builds the same shape on SQLite via metadata.create_all.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.DateTime(timezone=True)
_UUID = sa.Uuid(as_uuid=False)


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("workflow", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("started_at", _TS, nullable=True),
        sa.Column("finished_at", _TS, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed')",
            name="ck_runs_status",
        ),
        sa.CheckConstraint(
            "trigger IN ('scheduled', 'manual')", name="ck_runs_trigger"
        ),
    )
    op.create_index("ix_runs_workflow_status", "runs", ["workflow", "status"])
    op.create_index("ix_runs_created_at", "runs", ["created_at"])

    op.create_table(
        "outputs",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column(
            "run_id",
            _UUID,
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("data", JSONB(), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
    )
    op.create_index("ix_outputs_run_id", "outputs", ["run_id"])

    op.create_table(
        "schedules",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("workflow", sa.Text(), nullable=False),
        sa.Column("cron", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_run_at", _TS, nullable=True),
        sa.Column("next_run_at", _TS, nullable=True),
        sa.Column("created_at", _TS, nullable=False),
    )
    op.create_index(
        "ix_schedules_enabled_next_run", "schedules", ["enabled", "next_run_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_schedules_enabled_next_run", table_name="schedules")
    op.drop_table("schedules")
    op.drop_index("ix_outputs_run_id", table_name="outputs")
    op.drop_table("outputs")
    op.drop_index("ix_runs_created_at", table_name="runs")
    op.drop_index("ix_runs_workflow_status", table_name="runs")
    op.drop_table("runs")
