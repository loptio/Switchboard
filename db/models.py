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
    Integer,
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
# "awaiting_input" (Phase 5 Unit 3): a human-in-the-loop run is suspended at an
# interrupt, its graph state held by the checkpointer, waiting for `resume-run`.
RUN_STATUSES: tuple[str, ...] = (
    "pending",
    "running",
    "success",
    "failed",
    "awaiting_input",
)
RUN_TRIGGERS: tuple[str, ...] = ("scheduled", "manual")

# JSONB on Postgres (queryable), plain JSON elsewhere (SQLite stores it as TEXT).
_JSON = JSON().with_variant(JSONB(), "postgresql")
# tz-aware UTC timestamps; TIMESTAMP WITH TIME ZONE on Postgres.
_TS = DateTime(timezone=True)
# String-form UUIDs: native UUID on Postgres, CHAR(32) elsewhere.
_UUID = Uuid(as_uuid=False)
# Plain integer (per-run sequence counter).
_INT = Integer()


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
    # Phase 8 web human-in-the-loop: was the review gate requested for this run, and
    # the web-written decision the worker consumes to resume an awaiting_input run.
    Column("review", Boolean, nullable=True),
    Column("pending_decision", _JSON, nullable=True),
    # Phase 10b-1 per-run coding intake: the concrete task + the workspace (a real git
    # repo) for THIS coding run. NULL => fall back to Config (CODING_TASK /
    # CODING_WORKSPACE), preserving the 10a global-task behaviour. Ignored by non-coding
    # workflows.
    Column("coding_task", Text, nullable=True),
    Column("coding_workspace", Text, nullable=True),
    # Phase 11 observability: run-level metadata beyond status — the digest review
    # verdict (passed / accepted_at_cap / inconclusive / human_approved) and the email
    # delivery outcome (sent / skipped / failed). Nullable JSON so it is purely
    # additive; a run that records nothing leaves it NULL.
    Column("meta", _JSON, nullable=True),
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

# A run-node event = one transition of one workflow node during a run (Phase 11
# monitoring). The engine emits running/done/failed/awaiting as each node executes;
# the control plane reads the per-node latest to draw a live graph. `seq` is a
# per-run monotonic counter (set in the DAO) so events order deterministically even
# when timestamps tie (tests inject a fixed `now`). Best-effort — a failed write
# never breaks the run, so this is observability, not a correctness dependency.
NODE_EVENT_STATUSES: tuple[str, ...] = ("running", "done", "failed", "awaiting")
run_node_events = Table(
    "run_node_events",
    metadata,
    Column("id", _UUID, primary_key=True),
    Column(
        "run_id",
        _UUID,
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("node_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("seq", _INT, nullable=False),  # per-run monotonic order
    Column("at", _TS, nullable=False),
    CheckConstraint(_in_list("status", NODE_EVENT_STATUSES), name="ck_node_event_status"),
)
Index("ix_run_node_events_run_id", run_node_events.c.run_id)

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

# A User = the single login account for the control-plane API (Phase 3, Unit 1).
# Single-user by design: no roles. Passwords are bcrypt-hashed in the API layer
# (passlib) before they ever reach here — only the hash is stored.
users = Table(
    "users",
    metadata,
    Column("id", _UUID, primary_key=True),
    Column("username", Text, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("created_at", _TS, nullable=False),
)
Index("ix_users_username", users.c.username, unique=True)  # unique + fast lookup

# A WorkflowDef row = a workflow definition stored as DATA (Phase 8). The
# control-plane synthesizer writes these; the worker resolves a workflow by id
# (DB override, else the code default in workflows.WORKFLOWS). `def_id` is the
# logical workflow id (== definition["id"], e.g. "news"/"brief"/a user's id);
# `definition` is the serialized WorkflowDef JSON (workflows.workflow_def_to_dict).
# Edit-in-place (no versioning); built-ins are NOT seeded — an empty table means
# every workflow resolves to the code default (the no-regression safety net).
workflow_defs = Table(
    "workflow_defs",
    metadata,
    Column("id", _UUID, primary_key=True),
    Column("def_id", Text, nullable=False),  # logical id; == definition["id"]
    Column("name", Text, nullable=True),  # UI label
    Column("description", Text, nullable=True),
    Column("definition", _JSON, nullable=False),  # serialized WorkflowDef
    Column("created_at", _TS, nullable=False),
    Column("updated_at", _TS, nullable=True),
)
Index("ix_workflow_defs_def_id", workflow_defs.c.def_id, unique=True)

# An AgentDef row = an agent definition stored as DATA (Phase 8). Resolved by id
# (DB override, else the code default in agentdefs.AGENT_DEFS). `agent_id` ==
# definition["id"]; `definition` is the serialized AgentDef JSON.
agent_defs = Table(
    "agent_defs",
    metadata,
    Column("id", _UUID, primary_key=True),
    Column("agent_id", Text, nullable=False),  # logical id; == definition["id"]
    Column("name", Text, nullable=True),
    Column("description", Text, nullable=True),
    Column("definition", _JSON, nullable=False),  # serialized AgentDef
    Column("created_at", _TS, nullable=False),
    Column("updated_at", _TS, nullable=True),
)
Index("ix_agent_defs_agent_id", agent_defs.c.agent_id, unique=True)
