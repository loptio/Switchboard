"""Data-access layer package — Phase 2, Unit 1.

The shared DB contract for the whole system. Import what you need from `db`;
never import SQLAlchemy or touch the tables directly elsewhere:

    from db import create_run, save_output, update_run_status, list_due_schedules
"""

from __future__ import annotations

from .dao import (
    create_run,
    create_schedule,
    get_run,
    get_schedule,
    list_due_schedules,
    list_enabled_schedules,
    list_outputs,
    list_runs,
    list_schedules,
    mark_failed,
    mark_running,
    mark_schedule_ran,
    mark_success,
    save_output,
    set_schedule_enabled,
    update_run_status,
)
from .engine import configure, drop_db, get_engine, init_db
from .models import RUN_STATUSES, RUN_TRIGGERS, metadata
from .records import Output, Run, Schedule

__all__ = [
    # records
    "Run",
    "Output",
    "Schedule",
    # runs
    "create_run",
    "update_run_status",
    "mark_running",
    "mark_success",
    "mark_failed",
    "get_run",
    "list_runs",
    # outputs
    "save_output",
    "list_outputs",
    # schedules
    "create_schedule",
    "get_schedule",
    "list_schedules",
    "list_enabled_schedules",
    "list_due_schedules",
    "set_schedule_enabled",
    "mark_schedule_ran",
    # infrastructure / schema
    "configure",
    "get_engine",
    "init_db",
    "drop_db",
    "metadata",
    "RUN_STATUSES",
    "RUN_TRIGGERS",
]
