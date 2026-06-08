"""Runs + outputs endpoints (read here; manual trigger POST lives alongside).

All read-only over the data layer — the control plane shows status/output, it
never executes anything. Router-level deps require login on every endpoint and
enforce CSRF on writes (a no-op on these GETs).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

import db
from api.deps import get_current_user, require_csrf
from api.schemas import OutputOut, ResumeIn, RunCreate, RunOut

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
    review = body.review if body is not None else False
    coding_task = body.coding_task if body is not None else None
    coding_workspace = body.coding_workspace if body is not None else None
    return db.create_run(
        workflow=workflow,
        trigger="manual",
        review=review,
        coding_task=coding_task,
        coding_workspace=coding_workspace,
    )


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
    """A run's DELIVERABLE outputs (the rendered digest/brief), oldest first. 404 if
    the run is unknown; an empty list means it produced nothing yet. The human-review
    suspend payload (type="review") is excluded — it is state, not a product."""
    if db.get_run(run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return [o for o in db.list_outputs(run_id) if o.type != "review"]


@router.get("/{run_id}/review")
def get_run_review(run_id: str) -> dict:
    """The latest human-review payload ({digest, issues}) for an awaiting_input run,
    or {} if none. Persisted by the worker on suspend (a type="review" Output) since
    the candidate lives only in the langgraph checkpoint, which the web can't read."""
    if db.get_run(run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    reviews = [o for o in db.list_outputs(run_id) if o.type == "review"]
    return reviews[-1].data or {} if reviews else {}


@router.post("/{run_id}/resume", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
def resume_run(run_id: str, body: ResumeIn) -> db.Run:
    """Approve / redo an awaiting_input run — the web RESUME handoff. Records the
    decision; the worker claims it and resumes (the web never runs the agent). 404 if
    the run is unknown, 409 unless it is awaiting_input."""
    decision: dict = {"action": body.action}
    if body.feedback:
        decision["feedback"] = body.feedback
    try:
        return db.set_run_decision(run_id, decision)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
