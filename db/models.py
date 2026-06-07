"""SQLAlchemy schema — the single source of truth for the Phase 2 tables.

Dialect-portable on purpose: the same metadata builds Postgres-native DDL
(JSONB, TIMESTAMPTZ, native UUID) at runtime and a SQLite schema for the offline
tests. Value generation (ids, timestamps) lives in the data-access layer
(dao.py), not in column defaults, so behaviour is identical across backends.

The Alembic migration in alembic/versions/0001_initial.py mirrors these tables;
keep the two in sync (future schema changes are new migrations).
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    Table,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

metadata = MetaData()

# Enum-like value sets, enforced by CHECK constraints below AND validated in the
# data layer for clear errors. Defined here so schema, DAO and tests share one
# definition.
RUN_STATUSES: tuple[str, ...] = ("pending", "running", "success", "failed")
RUN_TRIGGERS: tuple[str, ...] = ("scheduled", "manual")

# JSONB on Postgres (queryable), plain JSON elsewhere (SQLite stores it as TEXT).
_JSON = JSON().with_variant(JSONB(), "postgresql")
# tz-aware UTC timestamps; TIMESTAMP WITH TIME ZONE on Postgres.
_TS = DateTime(timezone=True)
# String-form UUIDs: native UUID on Postgres, CHAR(32) elsewhere.
_UUID = Uuid(as_uuid=False)


def _in_list(col: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{col} IN ({quoted})"


# A Run = one execution of a workflow.
runs = Table(
    "runs",
    metadata,
    Column("id", _UUID, primary_key=True),
    Column("workflow", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("trigger", Text, nullable=False),
    Column("created_at", _TS, nullable=False),  # row created (pending)
    Column("started_at", _TS, nullable=True),  # set when status -> running
    Column("finished_at", _TS, nullable=True),  # set at a terminal state
    Column("error", Text, nullable=True),  # set on failure
    CheckConstraint(_in_list("status", RUN_STATUSES), name="ck_runs_status"),
    CheckConstraint(_in_list("trigger", RUN_TRIGGERS), name="ck_runs_trigger"),
)
Index("ix_runs_workflow_status", runs.c.workflow, runs.c.status)
Index("ix_runs_created_at", runs.c.created_at)

# An Output = an artifact produced by a Run (e.g. the rendered digest).
outputs = Table(
    "outputs",
    metadata,
    Column("id", _UUID, primary_key=True),
    Column(
        "run_id",
        _UUID,
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("type", Text, nullable=False),
    Column("content", Text, nullable=False),  # rendered markdown (what gets emailed)
    Column("data", _JSON, nullable=True),  # optional structured form (Phase 3 UI)
    Column("created_at", _TS, nullable=False),
)
Index("ix_outputs_run_id", outputs.c.run_id)

# A Schedule = a declarative cron schedule for a workflow (first-class data).
schedules = Table(
    "schedules",
    metadata,
    Column("id", _UUID, primary_key=True),
    Column("workflow", Text, nullable=False),
    Column("cron", Text, nullable=False),
    Column("timezone", Text, nullable=False),  # cron is meaningless without a tz
    Column("enabled", Boolean, nullable=False),
    Column("last_run_at", _TS, nullable=True),
    Column("next_run_at", _TS, nullable=True),  # maintained by the scheduler (Unit 2)
    Column("created_at", _TS, nullable=False),
)
Index("ix_schedules_enabled_next_run", schedules.c.enabled, schedules.c.next_run_at)
