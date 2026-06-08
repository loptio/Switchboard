"""Data-access layer package — Phase 2, Unit 1.

The shared DB contract for the whole system. Import what you need from `db`;
never import SQLAlchemy or touch the tables directly elsewhere:

    from db import create_run, save_output, update_run_status, list_due_schedules
"""

from __future__ import annotations

from .dao import (
    claim_next_pending_run,
    create_run,
    create_schedule,
    create_user,
    get_run,
    get_schedule,
    get_user,
    get_user_by_username,
    list_due_schedules,
    list_enabled_schedules,
    list_outputs,
    list_runs,
    list_schedules,
    mark_awaiting_input,
    mark_failed,
    mark_running,
    mark_schedule_ran,
    mark_success,
    save_output,
    set_schedule_enabled,
    set_user_password,
    update_run_status,
    update_schedule,
    delete_schedule,
    # workflow defs (Phase 8)
    create_workflow_def,
    get_workflow_def,
    list_workflow_defs,
    update_workflow_def,
    delete_workflow_def,
    # agent defs (Phase 8)
    create_agent_def,
    get_agent_def,
    list_agent_defs,
    update_agent_def,
    delete_agent_def,
)
from .engine import configure, drop_db, get_engine, init_db, is_configured
from .models import RUN_STATUSES, RUN_TRIGGERS, metadata
from .records import AgentDefRow, Output, Run, Schedule, User, WorkflowDefRow

__all__ = [
    # records
    "Run",
    "Output",
    "Schedule",
    "User",
    "WorkflowDefRow",
    "AgentDefRow",
    # runs
    "create_run",
    "update_run_status",
    "mark_running",
    "mark_success",
    "mark_failed",
    "mark_awaiting_input",
    "get_run",
    "list_runs",
    "claim_next_pending_run",
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
    "update_schedule",
    "delete_schedule",
    "mark_schedule_ran",
    # users
    "create_user",
    "get_user",
    "get_user_by_username",
    "set_user_password",
    # workflow defs (Phase 8)
    "create_workflow_def",
    "get_workflow_def",
    "list_workflow_defs",
    "update_workflow_def",
    "delete_workflow_def",
    # agent defs (Phase 8)
    "create_agent_def",
    "get_agent_def",
    "list_agent_defs",
    "update_agent_def",
    "delete_agent_def",
    # infrastructure / schema
    "configure",
    "get_engine",
    "init_db",
    "drop_db",
    "is_configured",
    "metadata",
    "RUN_STATUSES",
    "RUN_TRIGGERS",
]
