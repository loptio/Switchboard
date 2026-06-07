"""users table (control-plane auth)

Revision ID: 0002
Revises: 0001
Create Date: Phase 3, Unit 1 (backend API + auth)

Mirrors the `users` table added to db/models.py. Single login account; only the
bcrypt password hash is stored. Keep this in sync with models.py (future schema
changes are new migrations). Rendered for PostgreSQL; the offline test suite
builds the same shape on SQLite via metadata.create_all.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.DateTime(timezone=True)
_UUID = sa.Uuid(as_uuid=False)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
