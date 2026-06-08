"""Agent-definition CRUD — the synthesizer's data plane (Phase 8).

Symmetric to routers/workflows.py: control-plane only (db + pure-data agentdefs /
manifest / defs_validate). Built-in agent defs (agentdefs.AGENT_DEFS) are read-only;
clone to edit. Editing a (cloned) agent's system_prompt takes effect at runtime —
the worker resolves a node's agent_ref to its AgentDef and binds the prompt.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

import agentdefs
import db
import defs_validate
import manifest as _manifest
from api.deps import get_current_user, require_csrf
from api.schemas import AgentDefIn, AgentDefOut, AgentDefUpdate, CloneIn

router = APIRouter(
    prefix="/agents",
    tags=["agents"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)

_BUILTIN_IDS = set(agentdefs.AGENT_DEFS)
_MANIFEST = _manifest.build_manifest()


def _builtin_out(adef) -> AgentDefOut:
    return AgentDefOut(
        agent_id=adef.id,
        name=adef.id,
        definition=agentdefs.agent_def_to_dict(adef),
        builtin=True,
    )


def _row_out(row) -> AgentDefOut:
    return AgentDefOut(
        agent_id=row.agent_id,
        name=row.name,
        description=row.description,
        definition=row.definition,
        builtin=False,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _validate_or_400(definition: dict) -> None:
    errors = defs_validate.validate_agent_def(definition, _MANIFEST)
    if errors:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "; ".join(errors))


@router.get("", response_model=list[AgentDefOut])
def list_agents() -> list[AgentDefOut]:
    rows = db.list_agent_defs()
    shadowed = {r.agent_id for r in rows}
    out = [
        _builtin_out(a) for a in agentdefs.AGENT_DEFS.values() if a.id not in shadowed
    ]
    out += [_row_out(r) for r in rows]
    return out


@router.get("/{agent_id}", response_model=AgentDefOut)
def get_agent(agent_id: str) -> AgentDefOut:
    row = db.get_agent_def(agent_id)
    if row is not None:
        return _row_out(row)
    if agent_id in agentdefs.AGENT_DEFS:
        return _builtin_out(agentdefs.AGENT_DEFS[agent_id])
    raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")


@router.post("", response_model=AgentDefOut, status_code=status.HTTP_201_CREATED)
def create_agent(body: AgentDefIn) -> AgentDefOut:
    agent_id = body.definition.get("id")
    if not agent_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "definition.id is required")
    if agent_id in _BUILTIN_IDS:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{agent_id!r} is a built-in (read-only); clone it"
        )
    _validate_or_400(body.definition)
    try:
        row = db.create_agent_def(
            agent_id, body.definition, name=body.name, description=body.description
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _row_out(row)


@router.patch("/{agent_id}", response_model=AgentDefOut)
def update_agent(agent_id: str, body: AgentDefUpdate) -> AgentDefOut:
    if agent_id in _BUILTIN_IDS:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{agent_id!r} is a built-in (read-only); clone it"
        )
    if db.get_agent_def(agent_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
    fields = body.model_dump(exclude_unset=True)
    if "definition" in fields:
        if fields["definition"].get("id") != agent_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "definition.id must equal the path id"
            )
        _validate_or_400(fields["definition"])
    row = db.update_agent_def(
        agent_id,
        **{k: v for k, v in fields.items() if k in ("definition", "name", "description")},
    )
    return _row_out(row)


@router.post(
    "/{agent_id}/clone", response_model=AgentDefOut, status_code=status.HTTP_201_CREATED
)
def clone_agent(agent_id: str, body: CloneIn) -> AgentDefOut:
    row = db.get_agent_def(agent_id)
    if row is not None:
        source = row.definition
    elif agent_id in agentdefs.AGENT_DEFS:
        source = agentdefs.agent_def_to_dict(agentdefs.AGENT_DEFS[agent_id])
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
    if body.new_id in _BUILTIN_IDS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{body.new_id!r} is a built-in id")
    definition = dict(source)
    definition["id"] = body.new_id
    _validate_or_400(definition)
    try:
        new_row = db.create_agent_def(body.new_id, definition, name=body.name)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _row_out(new_row)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent(agent_id: str) -> None:
    if agent_id in _BUILTIN_IDS:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{agent_id!r} is a built-in (read-only)")
    try:
        db.delete_agent_def(agent_id)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
