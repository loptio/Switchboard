"""runs.awaiting_input status (human-in-the-loop)

Revision ID: 0003
Revises: 0002
Create Date: Phase 5, Unit 3 (human-in-the-loop suspend/resume)

Adds "awaiting_input" to the runs.status CHECK constraint, mirroring the new
value in db/models.py RUN_STATUSES. A run enters awaiting_input when its graph
is suspended at an interrupt (state held by the LangGraph checkpointer) waiting
for a human decision; `resume-run` moves it back to running.

Postgres can't alter a CHECK in place, so we drop and re-create it. The offline
test suite builds the same shape on SQLite via metadata.create_all. The status
list is snapshotted here on purpose (migrations are self-contained — they do not
import live models).
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = "status IN ('pending', 'running', 'success', 'failed')"
_NEW = "status IN ('pending', 'running', 'success', 'failed', 'awaiting_input')"


def upgrade() -> None:
    op.drop_constraint("ck_runs_status", "runs", type_="check")
    op.create_check_constraint("ck_runs_status", "runs", _NEW)


def downgrade() -> None:
    op.drop_constraint("ck_runs_status", "runs", type_="check")
    op.create_check_constraint("ck_runs_status", "runs", _OLD)
