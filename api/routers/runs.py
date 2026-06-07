"""Runs + outputs endpoints (read here; manual trigger POST lives alongside).

All read-only over the data layer — the control plane shows status/output, it
never executes anything. Router-level deps require login on every endpoint and
enforce CSRF on writes (a no-op on these GETs).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

import db
from api.deps import get_current_user, require_csrf
from api.schemas import OutputOut, RunCreate, RunOut

router = APIRouter(
    prefix="/runs",
    tags=["runs"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)


@router.post("", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
def trigger_run(body: RunCreate | None = None) -> db.Run:
    """Manual trigger — the web HANDOFF. Write a pending Run and return 202; the
    worker (scheduler heartbeat) claims and executes it. The web process never
    runs the agent here: it only records intent in the DB. See README "Phase 3".
    """
    workflow = body.workflow if body is not None else "news"
    return db.create_run(workflow=workflow, trigger="manual")


@router.get("", response_model=list[RunOut])
def list_runs(
    limit: int = Query(50, ge=1, le=200),
    status: str | None = None,
    workflow: str | None = None,
) -> list[db.Run]:
    """Recent runs, newest first. Optional status/workflow filters."""
    return db.list_runs(workflow=workflow, status=status, limit=limit)


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: str) -> db.Run:
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return run


@router.get("/{run_id}/output", response_model=list[OutputOut])
def get_run_output(run_id: str) -> list[db.Output]:
    """A run's outputs (the rendered digest), oldest first. 404 if the run is
    unknown; an empty list means the run exists but produced nothing yet."""
    if db.get_run(run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return db.list_outputs(run_id)
