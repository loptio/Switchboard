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

from .engine import get_engine
from .models import RUN_STATUSES, RUN_TRIGGERS, outputs, runs, schedules
from .records import Output, Run, Schedule

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
    now: datetime | None = None,
) -> Run:
    """Insert a new Run in the `pending` state and return it."""
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
