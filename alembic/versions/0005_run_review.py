"""runs.review + runs.pending_decision (Phase 8 web human-in-the-loop)

Revision ID: 0005
Revises: 0004
Create Date: Phase 8, Unit 3 (start review runs + resume from the web)

Adds two nullable columns to `runs`: `review` (was the human-review gate requested
for this run — the worker drives the interruptible path) and `pending_decision`
(the web-written approve/redo decision the worker consumes to resume an
awaiting_input run). Both nullable so the migration is safe on a populated table;
the data layer treats a NULL review as False. Mirrors db/models.py.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("runs", sa.Column("review", sa.Boolean(), nullable=True))
    op.add_column("runs", sa.Column("pending_decision", _JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "pending_decision")
    op.drop_column("runs", "review")
