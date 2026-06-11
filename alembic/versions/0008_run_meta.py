"""runs.meta (Phase 11 observability — run verdict + email delivery status)

Revision ID: 0008
Revises: 0007
Create Date: Phase 11 (observability unit)

Adds a nullable `meta` JSON column to `runs` holding run-level observability metadata
beyond status: the digest review verdict (passed / accepted_at_cap / inconclusive /
human_approved) and the email delivery outcome (sent / skipped / failed). Nullable so
the migration is safe on a populated table and the field is purely additive. JSONB on
Postgres, JSON elsewhere. Mirrors db/models.py.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("runs", sa.Column("meta", _JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "meta")
