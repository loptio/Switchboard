"""Workflow-definition CRUD — the synthesizer's data plane (Phase 8).

Control-plane ONLY: imports `db` + the pure-DATA def modules (`workflows`,
`manifest`, `defs_validate`) — never the engine/components (which pull
langgraph/the SDK). The no-SDK guard (tests/test_api_no_sdk.py) stays green.

- Built-in defs (workflows.WORKFLOWS) are READ-ONLY — they are the no-regression
  judge baseline (decision E). Editing one means cloning it to a new id.
- Saving VALIDATES the def against the manifest (guard #1) and rejects a broken def
  with 400 before it can ever reach the worker.
- "Run now" reuses the existing handoff: POST /runs {"workflow": <def_id>}.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

import db
import defs_validate
import manifest as _manifest
import workflows
from api.deps import get_current_user, require_csrf
from api.schemas import CloneIn, WorkflowDefIn, WorkflowDefOut, WorkflowDefUpdate

router = APIRouter(
    prefix="/workflows",
    tags=["workflows"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)

# All WORKFLOWS keys (incl. the "digest" alias) are reserved/read-only.
_BUILTIN_IDS = set(workflows.WORKFLOWS)
_MANIFEST = _manifest.build_manifest()


def _builtin_out(wf) -> WorkflowDefOut:
    return WorkflowDefOut(
        def_id=wf.id,
        name=wf.id,
        definition=workflows.workflow_def_to_dict(wf),
        builtin=True,
    )


def _row_out(row) -> WorkflowDefOut:
    return WorkflowDefOut(
        def_id=row.def_id,
        name=row.name,
        description=row.description,
        definition=row.definition,
        builtin=False,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _manifest_for_validation() -> dict:
    """The save-guard manifest with the agents namespace EXTENDED by the DB agent
    defs (Phase 9 U1): a workflow may bind any agent the runtime can resolve — a
    palette built-in or a DB-created AgentDef (defs_resolve resolves DB-only ids and
    the runner binds them through their built-in (builder, parser) pair)."""
    m = dict(_MANIFEST)
    m["agents"] = [*m["agents"], *(r.agent_id for r in db.list_agent_defs())]
    return m


def _validate_or_400(definition: dict) -> None:
    errors = defs_validate.validate_workflow_def(definition, _manifest_for_validation())
    if errors:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "; ".join(errors))


@router.get("", response_model=list[WorkflowDefOut])
def list_workflows() -> list[WorkflowDefOut]:
    """Built-in defs (read-only, deduped by id) + DB defs; a DB def shadows a
    built-in of the same id."""
    rows = db.list_workflow_defs()
    shadowed = {r.def_id for r in rows}
    builtins = {wf.id: wf for wf in workflows.WORKFLOWS.values()}
    out = [_builtin_out(wf) for wf in builtins.values() if wf.id not in shadowed]
    out += [_row_out(r) for r in rows]
    return out


@router.get("/{def_id}", response_model=WorkflowDefOut)
def get_workflow(def_id: str) -> WorkflowDefOut:
    row = db.get_workflow_def(def_id)
    if row is not None:
        return _row_out(row)
    if def_id in workflows.WORKFLOWS:
        return _builtin_out(workflows.WORKFLOWS[def_id])
    raise HTTPException(status.HTTP_404_NOT_FOUND, "workflow not found")


@router.post("", response_model=WorkflowDefOut, status_code=status.HTTP_201_CREATED)
def create_workflow(body: WorkflowDefIn) -> WorkflowDefOut:
    def_id = body.definition.get("id")
    if not def_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "definition.id is required")
    if def_id in _BUILTIN_IDS:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{def_id!r} is a built-in (read-only); clone it"
        )
    _validate_or_400(body.definition)
    try:
        row = db.create_workflow_def(
            def_id, body.definition, name=body.name, description=body.description
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _row_out(row)


@router.patch("/{def_id}", response_model=WorkflowDefOut)
def update_workflow(def_id: str, body: WorkflowDefUpdate) -> WorkflowDefOut:
    if def_id in _BUILTIN_IDS:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{def_id!r} is a built-in (read-only); clone it"
        )
    if db.get_workflow_def(def_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workflow not found")
    fields = body.model_dump(exclude_unset=True)
    if "definition" in fields:
        if fields["definition"].get("id") != def_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "definition.id must equal the path id"
            )
        _validate_or_400(fields["definition"])
    row = db.update_workflow_def(
        def_id,
        **{k: v for k, v in fields.items() if k in ("definition", "name", "description")},
    )
    return _row_out(row)


@router.post(
    "/{def_id}/clone", response_model=WorkflowDefOut, status_code=status.HTTP_201_CREATED
)
def clone_workflow(def_id: str, body: CloneIn) -> WorkflowDefOut:
    """Clone a built-in or DB def into a new editable def_id (decision E)."""
    row = db.get_workflow_def(def_id)
    if row is not None:
        source = row.definition
    elif def_id in workflows.WORKFLOWS:
        source = workflows.workflow_def_to_dict(workflows.WORKFLOWS[def_id])
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workflow not found")
    if body.new_id in _BUILTIN_IDS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{body.new_id!r} is a built-in id")
    definition = dict(source)
    definition["id"] = body.new_id
    _validate_or_400(definition)
    try:
        new_row = db.create_workflow_def(body.new_id, definition, name=body.name)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _row_out(new_row)


@router.delete("/{def_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workflow(def_id: str) -> None:
    if def_id in _BUILTIN_IDS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{def_id!r} is a built-in (read-only)")
    try:
        db.delete_workflow_def(def_id)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workflow not found")
