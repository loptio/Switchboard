"""Definition resolution (Phase 8) — DB override, else code default. Worker-side.

The runner resolves a workflow / agent by id at run start: a DB row (the synthesizer
wrote one) OVERRIDES the code default; an empty or unconfigured DB falls straight to
the code default (workflows.WORKFLOWS / agentdefs.AGENT_DEFS). This is the whole
no-regression safety net: offline tests run with no DB row → the code path → the
existing digest/brief/human-in-the-loop tests stay byte-for-byte green.

Lookups are gated on db.is_configured() (NOT get_engine(), which would lazily
connect) so an offline path with no engine configured — e.g. the agent unit tests
that call the real agents directly — never touches the DB; and any DB error degrades
to the code default rather than crashing a run.

Worker-side: imports db (web-safe) + the pure-data def modules. Never imported by the
web tier (the web reads defs as JSON via db directly; it never resolves-for-execution).
"""

from __future__ import annotations

import logging

import agentdefs
import db
import workflows

log = logging.getLogger(__name__)


def resolve_workflow_def(wf_id: str) -> workflows.WorkflowDef:
    """The WorkflowDef for `wf_id`: DB override, else the code default.

    Returns the code-default object by identity when no DB row exists, so the caller
    can tell "code path" (use the prebuilt module graph) from "DB override" (compile
    fresh) with an `is` check. Raises KeyError if neither exists.
    """
    row = _row(lambda: db.get_workflow_def(wf_id))
    if row is not None:
        return workflows.workflow_def_from_dict(row.definition)
    return workflows.WORKFLOWS[wf_id]


def resolve_agent_def(agent_id: str) -> agentdefs.AgentDef:
    """The AgentDef for `agent_id`: DB override, else the code default.

    Raises KeyError if neither a DB row nor a code default exists.
    """
    row = _row(lambda: db.get_agent_def(agent_id))
    if row is not None:
        return agentdefs.agent_def_from_dict(row.definition)
    return agentdefs.AGENT_DEFS[agent_id]


def _row(fetch):
    """Run a DB fetch only if an engine is configured; degrade any DB error to None
    (→ code default) so a missing table / unreachable DB never crashes a run."""
    if not db.is_configured():
        return None
    try:
        return fetch()
    except Exception as exc:  # noqa: BLE001 — degrade to the code default, never crash
        log.warning("DB def lookup failed; using code default: %s", exc)
        return None
