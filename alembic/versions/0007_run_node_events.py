"""run_node_events (Phase 11 — live per-node run monitoring)

Revision ID: 0007
Revises: 0006
Create Date: Phase 11 (workflow graph + live node monitoring)

Adds the `run_node_events` table: one row per node transition during a run
(running/done/failed/awaiting), emitted by the engine as each workflow node
executes. `seq` is a per-run monotonic counter (set in the DAO) so events order
deterministically even when `at` ties. ON DELETE CASCADE with runs (mirrors
outputs). Best-effort observability — never a correctness dependency. Mirrors
db/models.py.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STATUSES = ("running", "done", "failed", "awaiting")


def upgrade() -> None:
    op.create_table(
        "run_node_events",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column(
            "run_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in _STATUSES) + ")",
            name="ck_node_event_status",
        ),
    )
    op.create_index("ix_run_node_events_run_id", "run_node_events", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_run_node_events_run_id", table_name="run_node_events")
    op.drop_table("run_node_events")
