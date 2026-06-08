"""runs.coding_task + runs.coding_workspace (Phase 10b-1 per-run coding intake)

Revision ID: 0006
Revises: 0005
Create Date: Phase 10b-1, Unit 1 (per-run task/workspace for the coding family)

Adds two nullable columns to `runs`: `coding_task` (the concrete task for THIS coding
run) and `coding_workspace` (the workspace — a real git repo — it runs against). Both
nullable so the migration is safe on a populated table; a NULL value means the worker
falls back to Config (CODING_TASK / CODING_WORKSPACE), preserving the 10a global-task
behaviour. Mirrors the 0005 review/pending_decision pattern. Mirrors db/models.py.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("coding_task", sa.Text(), nullable=True))
    op.add_column("runs", sa.Column("coding_workspace", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "coding_workspace")
    op.drop_column("runs", "coding_task")
