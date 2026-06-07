"""Schedule CRUD endpoints (first-class schedule data over the DB).

The worker (scheduler) reads schedules from the DB every tick, so create/enable/
disable/edit here take effect WITHOUT restarting it — no in-memory schedule state
to invalidate. cron is validated and next_run_at primed via cronutil (SDK-free),
so this stays in the web tier and never imports the scheduler/runner.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

import db
from api.deps import get_current_user, require_csrf
from api.schemas import ScheduleCreate, ScheduleOut, ScheduleUpdate
from cronutil import compute_next_run, validate_cron

router = APIRouter(
    prefix="/schedules",
    tags=["schedules"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)


def _validated_next_run(cron: str, tz: str) -> datetime:
    """Validate cron/tz (400 on bad input) and return the primed next fire time."""
    try:
        validate_cron(cron, tz)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return compute_next_run(cron, tz, datetime.now(timezone.utc))


@router.get("", response_model=list[ScheduleOut])
def list_schedules() -> list[db.Schedule]:
    return db.list_schedules()


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
def create_schedule(body: ScheduleCreate) -> db.Schedule:
    # Prime next_run_at so creating a schedule doesn't trigger an immediate
    # catch-up run (mirrors scheduler.add_schedule, without importing it).
    next_run = _validated_next_run(body.cron, body.tz)
    return db.create_schedule(
        body.workflow,
        body.cron,
        tz=body.tz,
        enabled=body.enabled,
        next_run_at=next_run,
    )


@router.patch("/{schedule_id}", response_model=ScheduleOut)
def update_schedule(schedule_id: str, body: ScheduleUpdate) -> db.Schedule:
    existing = db.get_schedule(schedule_id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "schedule not found")

    fields = body.model_dump(exclude_unset=True)  # only what the client sent
    kwargs: dict = {}
    if "enabled" in fields:
        kwargs["enabled"] = fields["enabled"]
    if "cron" in fields:
        kwargs["cron"] = fields["cron"]
    if "tz" in fields:
        kwargs["tz"] = fields["tz"]
    # If the timing changed, re-validate and re-prime next_run_at.
    if "cron" in fields or "tz" in fields:
        new_cron = fields.get("cron", existing.cron)
        new_tz = fields.get("tz", existing.timezone)
        kwargs["next_run_at"] = _validated_next_run(new_cron, new_tz)

    return db.update_schedule(schedule_id, **kwargs)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(schedule_id: str) -> None:
    try:
        db.delete_schedule(schedule_id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "schedule not found") from exc
