"""Pydantic request/response models — the typed surface of the OpenAPI contract.

These map the data layer's frozen records (db.records) onto the JSON shapes the
frontend (Unit 2) depends on. Output models expose `from_record` classmethods so
routers translate records in one place.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

# from_attributes lets routers return the db.records dataclasses directly and
# have FastAPI validate/serialize them against these models (no manual mapping).
_FROM_RECORD = ConfigDict(from_attributes=True)


# --- auth ------------------------------------------------------------------

class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    username: str


# --- runs / outputs --------------------------------------------------------

class RunCreate(BaseModel):
    # Manual trigger payload — optional; defaults to the news workflow.
    workflow: str = "news"
    review: bool = False  # Phase 8: request the human-review gate (digest family)
    # Phase 10b-1 per-run coding intake (coding workflow only); NULL falls back to
    # Config (CODING_TASK / CODING_WORKSPACE) on the worker.
    coding_task: str | None = None
    coding_workspace: str | None = None


class ResumeIn(BaseModel):
    # Web human-in-the-loop decision for an awaiting_input run.
    action: str  # "approve" | "redo"
    feedback: str | None = None


class RunOut(BaseModel):
    model_config = _FROM_RECORD
    id: str
    workflow: str
    status: str
    trigger: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    # Phase 11 observability: {verdict, email} — run quality + delivery, NULL if none.
    meta: dict[str, Any] | None = None


class OutputOut(BaseModel):
    model_config = _FROM_RECORD
    id: str
    run_id: str
    type: str
    content: str
    data: dict[str, Any] | None
    created_at: datetime


class NodeEventOut(BaseModel):
    """One workflow-node transition during a run (Phase 11 live monitoring)."""

    model_config = _FROM_RECORD
    node_id: str
    status: str
    seq: int
    at: datetime


# --- schedules (CRUD) ------------------------------------------------------

class ScheduleOut(BaseModel):
    model_config = _FROM_RECORD
    id: str
    workflow: str
    cron: str
    timezone: str
    enabled: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime


class ScheduleCreate(BaseModel):
    cron: str
    workflow: str = "news"
    tz: str = "UTC"
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    # All optional — PATCH applies only the fields actually sent (exclude_unset).
    cron: str | None = None
    tz: str | None = None
    enabled: bool | None = None


# --- workflow / agent definitions (Phase 8 synthesizer) --------------------
# `definition` is the serialized WorkflowDef / AgentDef JSON; its `id` IS the
# logical def_id/agent_id. `builtin` flags code defaults (read-only — clone to edit).

class WorkflowDefOut(BaseModel):
    def_id: str
    name: str | None = None
    description: str | None = None
    definition: dict[str, Any]
    builtin: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkflowDefIn(BaseModel):
    definition: dict[str, Any]
    name: str | None = None
    description: str | None = None


class WorkflowDefUpdate(BaseModel):
    definition: dict[str, Any] | None = None
    name: str | None = None
    description: str | None = None


class AgentDefOut(BaseModel):
    agent_id: str
    name: str | None = None
    description: str | None = None
    definition: dict[str, Any]
    builtin: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AgentDefIn(BaseModel):
    definition: dict[str, Any]
    name: str | None = None
    description: str | None = None


class AgentDefUpdate(BaseModel):
    definition: dict[str, Any] | None = None
    name: str | None = None
    description: str | None = None


class CloneIn(BaseModel):
    new_id: str
    name: str | None = None
