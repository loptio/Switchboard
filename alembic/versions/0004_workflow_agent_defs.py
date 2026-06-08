"""workflow_defs and agent_defs tables (Phase 8)

Revision ID: 0004
Revises: 0003
Create Date: Phase 8, Unit 1 (definitions into DB + DB-override resolution)

Adds two tables storing workflow/agent definitions as JSON, so the control-plane
synthesizer can create/edit them and the worker resolves a def by id (DB override,
else the code default in workflows.WORKFLOWS / agentdefs.AGENT_DEFS). Mirrors
db/models.py; the offline suite builds the same shape on SQLite via
metadata.create_all. Built-ins are NOT seeded — an empty table means every def
resolves to its code default (the no-regression safety net). Types are redefined
here (migrations are self-contained — they don't import live models).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = sa.Uuid(as_uuid=False)
_TS = sa.DateTime(timezone=True)
_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "workflow_defs",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("def_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("definition", _JSON, nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=True),
    )
    op.create_index(
        "ix_workflow_defs_def_id", "workflow_defs", ["def_id"], unique=True
    )

    op.create_table(
        "agent_defs",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("definition", _JSON, nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=True),
    )
    op.create_index("ix_agent_defs_agent_id", "agent_defs", ["agent_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_agent_defs_agent_id", table_name="agent_defs")
    op.drop_table("agent_defs")
    op.drop_index("ix_workflow_defs_def_id", table_name="workflow_defs")
    op.drop_table("workflow_defs")
