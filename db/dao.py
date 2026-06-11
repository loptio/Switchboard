"""Data-access layer — the ONLY place in the system that touches the DB.

Everything else imports these functions (never SQLAlchemy, never the tables):

    from db import create_run, update_run_status, save_output, list_due_schedules

Contract notes:
- Every function opens its own transaction and returns a frozen dataclass record
  (records.py) — no SQLAlchemy objects or sessions leak to callers.
- Ids and timestamps are generated here (not in column defaults) so the returned
  record needs no extra round-trip and behaviour is identical on SQLite/Postgres.
- All datetimes are normalized to tz-aware UTC before they are written or
  compared. Pass aware UTC; a naive datetime is assumed to be UTC. (SQLite stores
  the wall-clock and drops tzinfo, so non-UTC inputs would otherwise corrupt
  comparisons — see the note in models.py / README.)
- `now` is injectable on writes (like Phase 1's write_digest(day)) so tests are
  deterministic without a time-mocking library.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from .engine import get_engine
from .models import (
    NODE_EVENT_STATUSES,
    RUN_STATUSES,
    RUN_TRIGGERS,
    agent_defs,
    outputs,
    run_node_events,
    runs,
    schedules,
    users,
    workflow_defs,
)
from .records import AgentDefRow, NodeEvent, Output, Run, Schedule, User, WorkflowDefRow

# Sentinel for "argument not supplied" where None is a meaningful value.
_UNSET = object()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(dt: datetime | None) -> datetime | None:
    """Coerce any datetime to tz-aware UTC; assume naive datetimes are UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _new_id() -> str:
    return str(uuid4())


def _is_uuid(value: object) -> bool:
    """Whether `value` is a valid UUID. A non-UUID id can never match a stored
    id, so callers treat it as 'not found' — and on PostgreSQL (native uuid
    column) this avoids a DataError from comparing against a malformed literal.
    """
    try:
        UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


# --- Runs -----------------------------------------------------------------

def create_run(
    workflow: str = "news",
    trigger: str = "manual",
    *,
    review: bool = False,
    coding_task: str | None = None,
    coding_workspace: str | None = None,
    now: datetime | None = None,
) -> Run:
    """Insert a new Run in the `pending` state and return it.

    `review` (Phase 8): request the human-review gate — the worker drives the
    interruptible path for this run (digest family only) instead of straight-through.

    `coding_task` / `coding_workspace` (Phase 10b-1): the per-run intake for a coding
    run. NULL leaves the worker to fall back to Config (CODING_TASK / CODING_WORKSPACE),
    preserving 10a behaviour; ignored by non-coding workflows.
    """
    if trigger not in RUN_TRIGGERS:
        raise ValueError(f"trigger must be one of {RUN_TRIGGERS}, got {trigger!r}")
    rid = _new_id()
    created = _to_utc(now) if now is not None else _now()
    with get_engine().begin() as conn:
        conn.execute(
            runs.insert().values(
                id=rid,
                workflow=workflow,
                status="pending",
                trigger=trigger,
                created_at=created,
                review=review,
                coding_task=coding_task,
                coding_workspace=coding_workspace,
            )
        )
    return Run(
        id=rid,
        workflow=workflow,
        status="pending",
        trigger=trigger,
        created_at=created,
        started_at=None,
        finished_at=None,
        error=None,
        review=review,
        coding_task=coding_task,
        coding_workspace=coding_workspace,
    )


def update_run_status(
    run_id: str,
    status: str,
    *,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    error: str | None = None,
) -> Run:
    """Update a Run's status (and optionally timestamps/error). Returns the Run.

    Only the fields you pass are changed; omit a field to leave it untouched.
    Raises ValueError on an unknown status, LookupError if the run is missing.
    """
    if status not in RUN_STATUSES:
        raise ValueError(f"status must be one of {RUN_STATUSES}, got {status!r}")
    if not _is_uuid(run_id):
        raise LookupError(f"No run with id {run_id!r}")
    values: dict = {"status": status}
    if started_at is not None:
        values["started_at"] = _to_utc(started_at)
    if finished_at is not None:
        values["finished_at"] = _to_utc(finished_at)
    if error is not None:
        values["error"] = error
    with get_engine().begin() as conn:
        result = conn.execute(update(runs).where(runs.c.id == run_id).values(**values))
        if result.rowcount == 0:
            raise LookupError(f"No run with id {run_id!r}")
        row = conn.execute(select(runs).where(runs.c.id == run_id)).mappings().one()
    return Run.from_row(row)


def mark_running(run_id: str, *, now: datetime | None = None) -> Run:
    """Convenience: status -> running, stamp started_at."""
    return update_run_status(run_id, "running", started_at=now or _now())


def mark_success(run_id: str, *, now: datetime | None = None) -> Run:
    """Convenience: status -> success, stamp finished_at."""
    return update_run_status(run_id, "success", finished_at=now or _now())


def mark_failed(run_id: str, error: str, *, now: datetime | None = None) -> Run:
    """Convenience: status -> failed, stamp finished_at, record the error."""
    return update_run_status(run_id, "failed", finished_at=now or _now(), error=error)


def mark_awaiting_input(run_id: str) -> Run:
    """Convenience: status -> awaiting_input (human-in-the-loop suspend).

    The run is paused at an interrupt waiting for a human decision; `resume-run`
    transitions it back to running. No timestamp change: started_at was stamped
    when the run first went running, and finished_at is for terminal states only.
    """
    return update_run_status(run_id, "awaiting_input")


def get_run(run_id: str) -> Run | None:
    if not _is_uuid(run_id):
        return None
    with get_engine().connect() as conn:
        row = conn.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
    return Run.from_row(row) if row else None


def list_runs(
    *,
    workflow: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[Run]:
    """List runs, newest first, optionally filtered by workflow/status."""
    stmt = select(runs)
    if workflow is not None:
        stmt = stmt.where(runs.c.workflow == workflow)
    if status is not None:
        stmt = stmt.where(runs.c.status == status)
    stmt = stmt.order_by(runs.c.created_at.desc()).limit(limit)
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [Run.from_row(r) for r in rows]


def claim_next_pending_run(*, now: datetime | None = None) -> Run | None:
    """Atomically claim the oldest pending Run (status pending -> running) and
    return it, or None when there are no pending runs.

    This is the worker side of the manual-trigger handoff: the web tier writes a
    pending Run (create_run) and the worker claims it here. The claim is a guarded
    UPDATE (WHERE id=? AND status='pending'); if another worker grabbed that row
    first (rowcount 0) we move on to the next pending row — so a run is executed
    exactly once even with overlapping ticks or multiple workers.
    """
    moment = _to_utc(now) if now is not None else _now()
    with get_engine().begin() as conn:
        while True:
            row = conn.execute(
                select(runs.c.id)
                .where(runs.c.status == "pending")
                .order_by(runs.c.created_at.asc())
                .limit(1)
            ).first()
            if row is None:
                return None
            run_id = row[0]
            claimed = conn.execute(
                update(runs)
                .where(runs.c.id == run_id, runs.c.status == "pending")
                .values(status="running", started_at=moment)
            )
            if claimed.rowcount == 1:
                stored = (
                    conn.execute(select(runs).where(runs.c.id == run_id))
                    .mappings()
                    .one()
                )
                return Run.from_row(stored)
            # rowcount 0: lost the race for this row — try the next pending one.


def set_run_decision(run_id: str, decision: dict) -> Run:
    """Record a human resume decision for an `awaiting_input` run (Phase 8 web->worker
    handoff). The worker claims it via claim_next_resumable_run and resumes. Raises
    LookupError if missing, ValueError if the run is not awaiting_input."""
    if not _is_uuid(run_id):
        raise LookupError(f"No run with id {run_id!r}")
    with get_engine().begin() as conn:
        row = conn.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
        if row is None:
            raise LookupError(f"No run with id {run_id!r}")
        if row["status"] != "awaiting_input":
            raise ValueError(f"run {run_id} is {row['status']!r}, not awaiting_input")
        conn.execute(
            update(runs).where(runs.c.id == run_id).values(pending_decision=decision)
        )
        updated = conn.execute(select(runs).where(runs.c.id == run_id)).mappings().one()
    return Run.from_row(updated)


def clear_run_decision(run_id: str) -> None:
    """Clear a consumed resume decision (the worker, after resuming)."""
    with get_engine().begin() as conn:
        conn.execute(
            update(runs).where(runs.c.id == run_id).values(pending_decision=None)
        )


def claim_next_resumable_run(*, now: datetime | None = None) -> Run | None:
    """Atomically claim the oldest `awaiting_input` run that has a pending decision
    (status awaiting_input -> running) and return it, or None. The worker half of the
    web resume handoff — mirrors claim_next_pending_run. started_at is left as-is (it
    was stamped on the run's first execution)."""
    with get_engine().begin() as conn:
        while True:
            row = conn.execute(
                select(runs.c.id)
                .where(
                    runs.c.status == "awaiting_input",
                    runs.c.pending_decision.isnot(None),
                )
                .order_by(runs.c.created_at.asc())
                .limit(1)
            ).first()
            if row is None:
                return None
            run_id = row[0]
            claimed = conn.execute(
                update(runs)
                .where(runs.c.id == run_id, runs.c.status == "awaiting_input")
                .values(status="running")
            )
            if claimed.rowcount == 1:
                stored = (
                    conn.execute(select(runs).where(runs.c.id == run_id))
                    .mappings()
                    .one()
                )
                return Run.from_row(stored)
            # rowcount 0: lost the race — try the next resumable run.


# --- Outputs --------------------------------------------------------------

def save_output(
    run_id: str,
    content: str,
    *,
    type: str = "digest",
    data: dict | None = None,
    now: datetime | None = None,
) -> Output:
    """Store an Output for a Run and return it.

    Validates that the run exists so both Postgres (FK) and SQLite (FKs off by
    default) give the same clear error.
    """
    if not _is_uuid(run_id):
        raise LookupError(f"No run with id {run_id!r}")
    oid = _new_id()
    created = _to_utc(now) if now is not None else _now()
    with get_engine().begin() as conn:
        exists = conn.execute(select(runs.c.id).where(runs.c.id == run_id)).first()
        if not exists:
            raise LookupError(f"No run with id {run_id!r}")
        conn.execute(
            outputs.insert().values(
                id=oid,
                run_id=run_id,
                type=type,
                content=content,
                data=data,
                created_at=created,
            )
        )
    return Output(
        id=oid,
        run_id=run_id,
        type=type,
        content=content,
        data=data,
        created_at=created,
    )


def list_outputs(run_id: str) -> list[Output]:
    """List a Run's outputs, oldest first."""
    if not _is_uuid(run_id):
        return []
    with get_engine().connect() as conn:
        rows = (
            conn.execute(
                select(outputs)
                .where(outputs.c.run_id == run_id)
                .order_by(outputs.c.created_at.asc())
            )
            .mappings()
            .all()
        )
    return [Output.from_row(r) for r in rows]


# --- Run node events (Phase 11 monitoring) --------------------------------

def record_node_event(
    run_id: str,
    node_id: str,
    status: str,
    *,
    now: datetime | None = None,
) -> NodeEvent | None:
    """Append a node-transition event for a run; return it (None if the run id is
    not a UUID — a defensive no-op, since this is best-effort observability).

    `seq` is a per-run monotonic counter computed here, so events order
    deterministically even when `at` ties (tests inject a fixed `now`). Safe under
    the single-worker sequential execution the system already relies on.
    """
    if status not in NODE_EVENT_STATUSES:
        raise ValueError(f"invalid node-event status {status!r}")
    if not _is_uuid(run_id):
        return None
    eid = _new_id()
    at = _to_utc(now) if now is not None else _now()
    from sqlalchemy import func

    with get_engine().begin() as conn:
        seq = (
            conn.execute(
                select(func.count())
                .select_from(run_node_events)
                .where(run_node_events.c.run_id == run_id)
            ).scalar()
            or 0
        )
        conn.execute(
            run_node_events.insert().values(
                id=eid,
                run_id=run_id,
                node_id=node_id,
                status=status,
                seq=seq,
                at=at,
            )
        )
    return NodeEvent(id=eid, run_id=run_id, node_id=node_id, status=status, seq=seq, at=at)


def list_node_events(run_id: str) -> list[NodeEvent]:
    """A run's node events in order (by seq). Empty for an unknown / non-UUID id."""
    if not _is_uuid(run_id):
        return []
    with get_engine().connect() as conn:
        rows = (
            conn.execute(
                select(run_node_events)
                .where(run_node_events.c.run_id == run_id)
                .order_by(run_node_events.c.seq.asc())
            )
            .mappings()
            .all()
        )
    return [NodeEvent.from_row(r) for r in rows]


# --- Schedules ------------------------------------------------------------

def create_schedule(
    workflow: str,
    cron: str,
    *,
    tz: str = "UTC",
    enabled: bool = True,
    next_run_at: datetime | None = None,
    now: datetime | None = None,
) -> Schedule:
    """Create a Schedule. `tz` is the timezone the cron expression is read in."""
    sid = _new_id()
    created = _to_utc(now) if now is not None else _now()
    next_run = _to_utc(next_run_at)
    with get_engine().begin() as conn:
        conn.execute(
            schedules.insert().values(
                id=sid,
                workflow=workflow,
                cron=cron,
                timezone=tz,
                enabled=enabled,
                last_run_at=None,
                next_run_at=next_run,
                created_at=created,
            )
        )
    return Schedule(
        id=sid,
        workflow=workflow,
        cron=cron,
        timezone=tz,
        enabled=enabled,
        last_run_at=None,
        next_run_at=next_run,
        created_at=created,
    )


def _update_schedule(schedule_id: str, values: dict) -> Schedule:
    if not _is_uuid(schedule_id):
        raise LookupError(f"No schedule with id {schedule_id!r}")
    with get_engine().begin() as conn:
        result = conn.execute(
            update(schedules).where(schedules.c.id == schedule_id).values(**values)
        )
        if result.rowcount == 0:
            raise LookupError(f"No schedule with id {schedule_id!r}")
        row = (
            conn.execute(select(schedules).where(schedules.c.id == schedule_id))
            .mappings()
            .one()
        )
    return Schedule.from_row(row)


def set_schedule_enabled(schedule_id: str, enabled: bool) -> Schedule:
    return _update_schedule(schedule_id, {"enabled": enabled})


def update_schedule(
    schedule_id: str,
    *,
    cron: str = _UNSET,  # type: ignore[assignment]
    tz: str = _UNSET,  # type: ignore[assignment]
    enabled: bool = _UNSET,  # type: ignore[assignment]
    next_run_at: datetime | None = _UNSET,  # type: ignore[assignment]
) -> Schedule:
    """Update any subset of a schedule's fields; omitted fields are unchanged.

    Raises LookupError if the schedule is missing. (cron validity is enforced in
    the API layer via cronutil; the data layer just stores what it's given.)
    """
    values: dict = {}
    if cron is not _UNSET:
        values["cron"] = cron
    if tz is not _UNSET:
        values["timezone"] = tz
    if enabled is not _UNSET:
        values["enabled"] = enabled
    if next_run_at is not _UNSET:
        values["next_run_at"] = _to_utc(next_run_at)
    if not values:  # nothing to change — return the current row (or 'missing')
        sched = get_schedule(schedule_id)
        if sched is None:
            raise LookupError(f"No schedule with id {schedule_id!r}")
        return sched
    return _update_schedule(schedule_id, values)


def delete_schedule(schedule_id: str) -> None:
    """Delete a schedule. Raises LookupError if it does not exist."""
    if not _is_uuid(schedule_id):
        raise LookupError(f"No schedule with id {schedule_id!r}")
    with get_engine().begin() as conn:
        result = conn.execute(
            schedules.delete().where(schedules.c.id == schedule_id)
        )
        if result.rowcount == 0:
            raise LookupError(f"No schedule with id {schedule_id!r}")


def mark_schedule_ran(
    schedule_id: str,
    *,
    last_run_at: datetime,
    next_run_at: datetime | None = _UNSET,  # type: ignore[assignment]
) -> Schedule:
    """Record that a schedule ran. Updates last_run_at; updates next_run_at only
    if supplied (pass None explicitly to clear it; omit to leave unchanged)."""
    values: dict = {"last_run_at": _to_utc(last_run_at)}
    if next_run_at is not _UNSET:
        values["next_run_at"] = _to_utc(next_run_at)
    return _update_schedule(schedule_id, values)


def get_schedule(schedule_id: str) -> Schedule | None:
    if not _is_uuid(schedule_id):
        return None
    with get_engine().connect() as conn:
        row = (
            conn.execute(select(schedules).where(schedules.c.id == schedule_id))
            .mappings()
            .first()
        )
    return Schedule.from_row(row) if row else None


def list_schedules() -> list[Schedule]:
    with get_engine().connect() as conn:
        rows = (
            conn.execute(select(schedules).order_by(schedules.c.created_at.asc()))
            .mappings()
            .all()
        )
    return [Schedule.from_row(r) for r in rows]


def list_enabled_schedules() -> list[Schedule]:
    """All enabled schedules (e.g. for the scheduler to arm triggers)."""
    with get_engine().connect() as conn:
        rows = (
            conn.execute(
                select(schedules)
                .where(schedules.c.enabled.is_(True))
                .order_by(schedules.c.created_at.asc())
            )
            .mappings()
            .all()
        )
    return [Schedule.from_row(r) for r in rows]


def list_due_schedules(now: datetime) -> list[Schedule]:
    """Enabled schedules whose next_run_at has arrived (or was never set).

    `now` is passed in (not read from the clock) so the scheduler can test
    against mock time. next_run_at is maintained by the scheduler (Unit 2);
    a NULL next_run_at counts as due (e.g. a freshly created schedule).
    """
    moment = _to_utc(now)
    with get_engine().connect() as conn:
        rows = (
            conn.execute(
                select(schedules)
                .where(
                    schedules.c.enabled.is_(True),
                    or_(
                        schedules.c.next_run_at.is_(None),
                        schedules.c.next_run_at <= moment,
                    ),
                )
                .order_by(schedules.c.created_at.asc())
            )
            .mappings()
            .all()
        )
    return [Schedule.from_row(r) for r in rows]


# --- Users ----------------------------------------------------------------
# The control-plane API (Phase 3) authenticates against these. Only the bcrypt
# hash is stored; hashing/verification happens in the API layer, never here.

def create_user(
    username: str, password_hash: str, *, now: datetime | None = None
) -> User:
    """Create the login user. Raises ValueError if the username already exists."""
    uid = _new_id()
    created = _to_utc(now) if now is not None else _now()
    try:
        with get_engine().begin() as conn:
            conn.execute(
                users.insert().values(
                    id=uid,
                    username=username,
                    password_hash=password_hash,
                    created_at=created,
                )
            )
    except IntegrityError as exc:
        raise ValueError(f"user {username!r} already exists") from exc
    return User(
        id=uid, username=username, password_hash=password_hash, created_at=created
    )


def get_user(user_id: str) -> User | None:
    if not _is_uuid(user_id):
        return None
    with get_engine().connect() as conn:
        row = conn.execute(select(users).where(users.c.id == user_id)).mappings().first()
    return User.from_row(row) if row else None


def get_user_by_username(username: str) -> User | None:
    with get_engine().connect() as conn:
        row = (
            conn.execute(select(users).where(users.c.username == username))
            .mappings()
            .first()
        )
    return User.from_row(row) if row else None


def set_user_password(username: str, password_hash: str) -> User:
    """Update an existing user's password hash. Raises LookupError if missing."""
    with get_engine().begin() as conn:
        result = conn.execute(
            update(users)
            .where(users.c.username == username)
            .values(password_hash=password_hash)
        )
        if result.rowcount == 0:
            raise LookupError(f"No user named {username!r}")
        row = (
            conn.execute(select(users).where(users.c.username == username))
            .mappings()
            .one()
        )
    return User.from_row(row)


# --- Workflow definitions (Phase 8) ---------------------------------------
# Stored as data; the worker resolves a workflow by id (DB override else the code
# default in workflows.WORKFLOWS). `def_id` is an arbitrary logical string (not a
# UUID), so lookups need no _is_uuid guard — a non-matching key just returns None.

def create_workflow_def(
    def_id: str,
    definition: dict,
    *,
    name: str | None = None,
    description: str | None = None,
    now: datetime | None = None,
) -> WorkflowDefRow:
    """Insert a workflow definition. Raises ValueError if `def_id` already exists."""
    rid = _new_id()
    created = _to_utc(now) if now is not None else _now()
    try:
        with get_engine().begin() as conn:
            conn.execute(
                workflow_defs.insert().values(
                    id=rid,
                    def_id=def_id,
                    name=name,
                    description=description,
                    definition=definition,
                    created_at=created,
                    updated_at=None,
                )
            )
    except IntegrityError as exc:
        raise ValueError(f"workflow def {def_id!r} already exists") from exc
    return WorkflowDefRow(
        id=rid,
        def_id=def_id,
        name=name,
        description=description,
        definition=definition,
        created_at=created,
        updated_at=None,
    )


def get_workflow_def(def_id: str) -> WorkflowDefRow | None:
    """Fetch a workflow def by its logical id, or None."""
    with get_engine().connect() as conn:
        row = (
            conn.execute(
                select(workflow_defs).where(workflow_defs.c.def_id == def_id)
            )
            .mappings()
            .first()
        )
    return WorkflowDefRow.from_row(row) if row else None


def list_workflow_defs() -> list[WorkflowDefRow]:
    """All workflow defs, oldest first."""
    with get_engine().connect() as conn:
        rows = (
            conn.execute(
                select(workflow_defs).order_by(workflow_defs.c.created_at.asc())
            )
            .mappings()
            .all()
        )
    return [WorkflowDefRow.from_row(r) for r in rows]


def update_workflow_def(
    def_id: str,
    *,
    definition: dict = _UNSET,  # type: ignore[assignment]
    name: str | None = _UNSET,  # type: ignore[assignment]
    description: str | None = _UNSET,  # type: ignore[assignment]
    now: datetime | None = None,
) -> WorkflowDefRow:
    """Update a workflow def's fields (omitted = unchanged); stamps updated_at.

    Raises LookupError if the def_id is unknown.
    """
    values: dict = {"updated_at": _to_utc(now) if now is not None else _now()}
    if definition is not _UNSET:
        values["definition"] = definition
    if name is not _UNSET:
        values["name"] = name
    if description is not _UNSET:
        values["description"] = description
    with get_engine().begin() as conn:
        result = conn.execute(
            update(workflow_defs)
            .where(workflow_defs.c.def_id == def_id)
            .values(**values)
        )
        if result.rowcount == 0:
            raise LookupError(f"No workflow def with def_id {def_id!r}")
        row = (
            conn.execute(
                select(workflow_defs).where(workflow_defs.c.def_id == def_id)
            )
            .mappings()
            .one()
        )
    return WorkflowDefRow.from_row(row)


def delete_workflow_def(def_id: str) -> None:
    """Delete a workflow def. Raises LookupError if it does not exist."""
    with get_engine().begin() as conn:
        result = conn.execute(
            workflow_defs.delete().where(workflow_defs.c.def_id == def_id)
        )
        if result.rowcount == 0:
            raise LookupError(f"No workflow def with def_id {def_id!r}")


# --- Agent definitions (Phase 8) ------------------------------------------
# Symmetric to workflow_defs; resolved by id (DB override else agentdefs.AGENT_DEFS).

def create_agent_def(
    agent_id: str,
    definition: dict,
    *,
    name: str | None = None,
    description: str | None = None,
    now: datetime | None = None,
) -> AgentDefRow:
    """Insert an agent definition. Raises ValueError if `agent_id` already exists."""
    rid = _new_id()
    created = _to_utc(now) if now is not None else _now()
    try:
        with get_engine().begin() as conn:
            conn.execute(
                agent_defs.insert().values(
                    id=rid,
                    agent_id=agent_id,
                    name=name,
                    description=description,
                    definition=definition,
                    created_at=created,
                    updated_at=None,
                )
            )
    except IntegrityError as exc:
        raise ValueError(f"agent def {agent_id!r} already exists") from exc
    return AgentDefRow(
        id=rid,
        agent_id=agent_id,
        name=name,
        description=description,
        definition=definition,
        created_at=created,
        updated_at=None,
    )


def get_agent_def(agent_id: str) -> AgentDefRow | None:
    """Fetch an agent def by its logical id, or None."""
    with get_engine().connect() as conn:
        row = (
            conn.execute(select(agent_defs).where(agent_defs.c.agent_id == agent_id))
            .mappings()
            .first()
        )
    return AgentDefRow.from_row(row) if row else None


def list_agent_defs() -> list[AgentDefRow]:
    """All agent defs, oldest first."""
    with get_engine().connect() as conn:
        rows = (
            conn.execute(select(agent_defs).order_by(agent_defs.c.created_at.asc()))
            .mappings()
            .all()
        )
    return [AgentDefRow.from_row(r) for r in rows]


def update_agent_def(
    agent_id: str,
    *,
    definition: dict = _UNSET,  # type: ignore[assignment]
    name: str | None = _UNSET,  # type: ignore[assignment]
    description: str | None = _UNSET,  # type: ignore[assignment]
    now: datetime | None = None,
) -> AgentDefRow:
    """Update an agent def's fields (omitted = unchanged); stamps updated_at.

    Raises LookupError if the agent_id is unknown.
    """
    values: dict = {"updated_at": _to_utc(now) if now is not None else _now()}
    if definition is not _UNSET:
        values["definition"] = definition
    if name is not _UNSET:
        values["name"] = name
    if description is not _UNSET:
        values["description"] = description
    with get_engine().begin() as conn:
        result = conn.execute(
            update(agent_defs).where(agent_defs.c.agent_id == agent_id).values(**values)
        )
        if result.rowcount == 0:
            raise LookupError(f"No agent def with agent_id {agent_id!r}")
        row = (
            conn.execute(select(agent_defs).where(agent_defs.c.agent_id == agent_id))
            .mappings()
            .one()
        )
    return AgentDefRow.from_row(row)


def delete_agent_def(agent_id: str) -> None:
    """Delete an agent def. Raises LookupError if it does not exist."""
    with get_engine().begin() as conn:
        result = conn.execute(
            agent_defs.delete().where(agent_defs.c.agent_id == agent_id)
        )
        if result.rowcount == 0:
            raise LookupError(f"No agent def with agent_id {agent_id!r}")
