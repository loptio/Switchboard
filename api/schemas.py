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


class OutputOut(BaseModel):
    model_config = _FROM_RECORD
    id: str
    run_id: str
    type: str
    content: str
    data: dict[str, Any] | None
    created_at: datetime


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
