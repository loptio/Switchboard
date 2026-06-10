"""Meta orchestrator — the meta family (Phase 9), run by the GENERIC engine.

A new FAMILY (blueprint decision 14: a genuinely new shape = new CODE, like the
coding family — not data). Its graph is META_DEF (workflows.py) compiled by
`engine.build_graph`; node behaviour lives in handlers registered LOCALLY (not in
the shared `components` registries — the meta family is a worker-side island, so
the Phase 8 manifest/validator never see meta_* names and a proposal cannot draft
meta workflows).

Graph (compiled from META_DEF):
    START → draft → validate ─(errors, attempts left)→ draft   (bounded redo)
                       │(errors, attempts exhausted)→ END       (give_up)
                       └(valid)→ human_review ─(approve)→ END
                                       └(redo + feedback)→ draft

- draft        : the meta_agent drafting seam (config["configurable"]["draft_fn"],
                 injectable — tests use scripted fakes, no SDK / no key / no spend).
                 A malformed reply (AgentContractError) counts as a failed attempt.
- validate     : DETERMINISTIC (no LLM) — meta_agent.validate_proposal against the
                 live palette + the CURRENT taken-id sets (built-ins ∪ DB).
- human_review : interrupt() with the proposal payload; approve / redo(+feedback).
                 Side-effect-free before the interrupt (LangGraph replays the node
                 on resume). Persistence happens in runner._finalize_meta, after
                 the graph completes approved — never here.

There is deliberately NO non-review build_meta: a meta run without the human gate
must not exist (the runner refuses it), so this module only exposes the
interruptible start/resume pair, mirroring the coding family's review surface.
State is JSON-native dict-state (checkpointer-serializable), like digest/brief/coding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TypedDict

from langgraph.types import Command, interrupt

import agentdefs
import db
import engine
import manifest
import meta_agent
import workflows
from agent import AgentContractError
from config import load_config
from workflows import META_DEF, WorkflowDef

log = logging.getLogger(__name__)

# Mirrors META_DEF.params (data); this module's defaults must agree with the
# WorkflowDef so the prebuilt module graph and a params override stay consistent.
DEFAULT_MAX_REDOS = META_DEF.params["max_redos"]


# --- LangGraph state ---------------------------------------------------------
class _State(TypedDict):
    """Graph state — JSON-native only (NO dataclasses, NO callables; the drafting
    seam is injected via config)."""

    request: str
    model: str
    max_redos: int
    attempts: int  # drafts so far; a human redo resets the budget
    proposal: dict | None  # {"workflow_def": ..., "agent_defs": [...], "explanation": ...}
    errors: list  # validation errors for the CURRENT proposal ([] = valid)
    feedback: str | None  # human redo feedback, consumed by the next draft
    approved: bool


def existing_def_ids() -> tuple[set, set]:
    """The TAKEN (workflow_ids, agent_ids): code built-ins ∪ DB rows. DB lookups are
    gated on db.is_configured() and degrade to built-ins-only on any DB error — the
    defs_resolve posture; the finalize re-check catches anything missed here."""
    wf_ids = set(workflows.WORKFLOWS)
    ag_ids = set(agentdefs.AGENT_DEFS)
    if db.is_configured():
        try:
            wf_ids |= {r.def_id for r in db.list_workflow_defs()}
            ag_ids |= {r.agent_id for r in db.list_agent_defs()}
        except Exception as exc:  # noqa: BLE001 — degrade, never crash a run
            log.warning("meta: DB id listing failed; using built-ins only: %s", exc)
    return wf_ids, ag_ids


def default_draft_fn(
    request: str,
    *,
    model: str,
    prior: dict | None = None,
    errors: list | None = None,
    feedback: str | None = None,
) -> dict:
    """The real drafting seam: gather the palette + taken ids, then run the
    meta_agent through llm.complete. Tests inject scripted fakes instead."""
    wf_ids, ag_ids = existing_def_ids()
    return meta_agent.draft_proposal(
        request,
        model=model,
        palette=manifest.build_manifest(),
        existing_workflow_ids=wf_ids,
        existing_agent_ids=ag_ids,
        prior=prior,
        errors=errors,
        feedback=feedback,
        language=load_config().output_language,
    )


# --- node handlers -----------------------------------------------------------
def _draft_node(state: _State, config) -> dict:
    """One drafting attempt via the injected seam. A contract-violating reply is a
    FAILED ATTEMPT (errors recorded, proposal cleared), not a crash — it rides the
    same bounded-redo loop as a validation failure."""
    draft_fn = config["configurable"]["draft_fn"]
    try:
        proposal = draft_fn(
            state["request"],
            model=state["model"],
            prior=state.get("proposal"),
            errors=state.get("errors") or None,
            feedback=state.get("feedback"),
        )
    except AgentContractError as exc:
        log.warning("meta draft attempt %d violated the contract: %s",
                    state.get("attempts", 0) + 1, exc)
        return {
            "proposal": None,
            "attempts": state.get("attempts", 0) + 1,
            "feedback": None,
            "errors": [f"draft reply violated the proposal contract: {exc}"],
        }
    log.info(
        "meta draft attempt %d proposed workflow %r",
        state.get("attempts", 0) + 1,
        (proposal.get("workflow_def") or {}).get("id"),
    )
    # Feedback/errors are consumed by this draft; validate refills errors next.
    return {
        "proposal": proposal,
        "attempts": state.get("attempts", 0) + 1,
        "feedback": None,
        "errors": [],
    }


def _validate_node(state: _State) -> dict:
    """Deterministic proposal check (no LLM): the Phase 8 def guards + the meta-only
    rules, against the palette and the CURRENT taken-id sets."""
    if state.get("proposal") is None:
        return {}  # the draft itself failed; its contract error is already recorded
    wf_ids, ag_ids = existing_def_ids()
    errors = meta_agent.validate_proposal(
        state["proposal"],
        palette=manifest.build_manifest(),
        existing_workflow_ids=wf_ids,
        existing_agent_ids=ag_ids,
    )
    if errors:
        log.info("meta validate: %d error(s) on attempt %d", len(errors), state.get("attempts", 0))
    return {"errors": errors}


def _human_review_node(state: _State) -> dict:
    """The human approval gate (the Phase 9 guardrail): interrupt() with the
    validated proposal; approve → done, anything else → redo with optional feedback
    and a FRESH draft budget (attempts reset, mirroring the digest gate).

    PURE before interrupt(): LangGraph re-runs this node from the top on resume, so
    nothing here may have side effects — persistence lives in runner._finalize_meta."""
    proposal = state.get("proposal") or {}
    payload = {
        "proposal": {
            "request": state.get("request", ""),
            "workflow_def": proposal.get("workflow_def"),
            "agent_defs": proposal.get("agent_defs") or [],
            "explanation": proposal.get("explanation", ""),
            "attempts": state.get("attempts", 0),
        }
    }
    decision = interrupt(payload)
    # --- resumed via Command(resume=decision) ---
    action = decision.get("action") if isinstance(decision, dict) else decision
    if action == "approve":
        return {"approved": True}
    text = decision.get("feedback") if isinstance(decision, dict) else None
    return {"approved": False, "feedback": text, "errors": [], "attempts": 0}


def _route_after_validate(state: _State) -> str:
    if not state.get("errors"):
        return "human_review"
    if state.get("attempts", 0) <= state.get("max_redos", DEFAULT_MAX_REDOS):
        return "draft"
    return "give_up"


def _route_after_human_review(state: _State) -> str:
    return "end" if state.get("approved") else "draft"


# --- the local registries (NOT components.*: meta is a worker-side island) ----
_NODE_HANDLERS: dict = {
    "meta_draft": _draft_node,
    "meta_validate": _validate_node,
    "meta_human_review": _human_review_node,
}
_PREDICATES: dict = {
    "meta_route_after_validate": _route_after_validate,
    "meta_route_after_human_review": _route_after_human_review,
}


def _builder_for(wf: WorkflowDef | None):
    """The prebuilt module builder for the code default (wf is None), else a fresh
    build using the LOCAL meta registries. The meta family is never web-synthesized,
    so in practice wf is always None — the override path exists for symmetry/tests."""
    if wf is None:
        return _BUILDER
    return engine.build_graph(wf, _State, node_handlers=_NODE_HANDLERS, predicates=_PREDICATES)


_BUILDER = engine.build_graph(
    META_DEF, _State, node_handlers=_NODE_HANDLERS, predicates=_PREDICATES
)


def _initial_state(request: str, model: str, *, max_redos: int) -> _State:
    return {
        "request": request,
        "model": model,
        "max_redos": max_redos,
        "attempts": 0,
        "proposal": None,
        "errors": [],
        "feedback": None,
        "approved": False,
    }


# --- human-in-the-loop: the interruptible proposal review ---------------------
@dataclass(frozen=True)
class MetaReviewOutcome:
    """Outcome of an interruptible meta run.

    - status="suspended": paused at the proposal gate; `payload` is the review
      contract {"proposal": {request, workflow_def, agent_defs, explanation,
      attempts}} (what the web ReviewPanel renders). Resume with
      resume_meta_review_run.
    - status="completed": `result` is the final state subset {"approved",
      "proposal", "errors", "attempts", "request"} — approved=False means the
      bounded redo budget ran out (give_up); the runner marks it failed and never
      persists.
    """

    status: str
    payload: dict | None = None
    result: dict | None = None


def _meta_config(thread_id: str, draft_fn) -> dict:
    return {"configurable": {"thread_id": thread_id, "draft_fn": draft_fn}}


def _outcome_from_state(final: dict) -> MetaReviewOutcome:
    interrupts = final.get("__interrupt__")
    if interrupts:
        return MetaReviewOutcome(status="suspended", payload=interrupts[0].value)
    return MetaReviewOutcome(
        status="completed",
        result={
            "approved": bool(final.get("approved")),
            "proposal": final.get("proposal"),
            "errors": list(final.get("errors") or []),
            "attempts": final.get("attempts", 0),
            "request": final.get("request", ""),
        },
    )


def start_meta_review_run(
    request: str,
    *,
    model: str,
    thread_id: str,
    checkpointer,
    max_redos: int = DEFAULT_MAX_REDOS,
    draft_fn=default_draft_fn,
    wf: WorkflowDef | None = None,
) -> MetaReviewOutcome:
    """Run the meta graph (gate always ON — the family has no gateless path),
    persisting to `checkpointer` under `thread_id`. Returns suspended (a validated
    proposal awaits approval) or completed (give_up: the redo budget ran out)."""
    app = _builder_for(wf).compile(checkpointer=checkpointer)
    final = app.invoke(
        _initial_state(request, model, max_redos=max_redos),
        config=_meta_config(thread_id, draft_fn),
    )
    return _outcome_from_state(final)


def resume_meta_review_run(
    *,
    thread_id: str,
    checkpointer,
    decision: dict,
    draft_fn=default_draft_fn,
    wf: WorkflowDef | None = None,
) -> MetaReviewOutcome:
    """Resume a suspended meta run, injecting `decision` into the waiting
    interrupt() ({"action": "approve"} or {"action": "redo", "feedback": "..."}).
    The config (seam + thread_id) is RE-INJECTED — callables are never persisted.
    A redo re-drafts with the feedback and re-presents (suspended again); approve
    completes (the runner then persists the defs)."""
    app = _builder_for(wf).compile(checkpointer=checkpointer)
    final = app.invoke(Command(resume=decision), config=_meta_config(thread_id, draft_fn))
    return _outcome_from_state(final)
