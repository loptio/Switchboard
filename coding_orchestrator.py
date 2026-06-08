"""Coding orchestrator — the coding family, run by the GENERIC engine (Phase 10a).

A new FAMILY (blueprint decision 14: a genuinely new shape = new CODE, like digest /
brief — not data). `build_coding` is symmetric to `build_digest` / `build_brief`: a
task + a workspace go in, a `CodingResult` (summary + diff + changed_files + status)
comes out. Its graph is the `CODING_DEF` WorkflowDef (workflows.py) compiled by
`engine.build_graph`; the node behaviour lives in handlers registered LOCALLY (not in
the shared `components` registries — the coding family is code, not a web-synthesizable
def, so it stays a worker-side island and the Phase 8 manifest/validator are untouched).

Graph (U1, compiled from CODING_DEF):
    START → coding → finalize_gate → END
      coding       : run ONE bounded, workspace-confined agent loop via the seam   (coding_agent node)
      finalize_gate: no-op convergence point (U2 attaches the human-review gate here)

The agent loop itself lives entirely in the `coding_agent.run_coding_agent` SEAM (the
only Agent SDK caller); the engine just treats `coding` as a node that runs a handler.
The seam is INJECTED via LangGraph's per-invoke `config["configurable"]["coding_fn"]`
(default the real seam; tests pass a deterministic fake), so the whole family runs
offline with no SDK / no key / no spend. State is JSON-native (dict-state), matching
digest/brief: the handler converts to/from the CodingResult dataclass at its boundary.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import TypedDict

from langgraph.types import Command, interrupt

import engine
import workspace
from coding_agent import CodingResult, run_coding_agent
from workflows import CODING_DEF, WorkflowDef

log = logging.getLogger(__name__)

# Defaults mirror CODING_DEF.params (data); build_coding's defaults must agree with the
# WorkflowDef so the prebuilt module graph and a params override stay consistent.
DEFAULT_MAX_TURNS = CODING_DEF.params["max_turns"]
DEFAULT_MAX_TOOL_CALLS = CODING_DEF.params["max_tool_calls"]
DEFAULT_MAX_BUDGET_USD = CODING_DEF.params["max_budget_usd"]


# --- dict-state converters --------------------------------------------------
def _result_to_dict(result: CodingResult) -> dict:
    return asdict(result)


def _result_from_dict(d: dict) -> CodingResult:
    return CodingResult(
        summary=d.get("summary", ""),
        diff=d.get("diff", ""),
        changed_files=list(d.get("changed_files") or []),
        status=d.get("status", "completed"),
        turns=d.get("turns", 0),
        tool_calls=d.get("tool_calls", 0),
        cost_usd=d.get("cost_usd"),
        commands=list(d.get("commands") or []),
        git_tampered=list(d.get("git_tampered") or []),
    )


# --- LangGraph state --------------------------------------------------------
class _State(TypedDict):
    """Graph state — JSON-native only (checkpointer-serializable for U2's review gate).
    NO dataclasses, NO callables (the seam is injected via config)."""

    task: str
    workspace: str
    model: str
    feedback: str | None  # human redo feedback (U2); appended to the task by the seam
    max_turns: int
    max_tool_calls: int
    max_budget_usd: float | None
    result: dict | None  # serialized CodingResult — the answer
    review: bool  # human-in-the-loop gate on? (U2; default False)
    approved: bool  # has the human approved the diff? (U2)


# --- node handlers ----------------------------------------------------------
def _coding_node(state: _State, config) -> dict:
    """Run one bounded coding-agent loop via the injected seam, store the result."""
    coding_fn = config["configurable"]["coding_fn"]
    ws = state["workspace"]
    is_git = workspace.is_git_repo(ws)
    # Phase 10b-2: snapshot the security-relevant `.git` BEFORE the run, so a command
    # that injects a hook / poisons config is caught (the un-sandboxable code-exec vector
    # git diff & git restore can't see). Taken before the seam runs.
    git_before = workspace.git_security_snapshot(ws) if is_git else None
    result = coding_fn(
        state["task"],
        ws,
        model=state["model"],
        max_turns=state["max_turns"],
        max_tool_calls=state["max_tool_calls"],
        max_budget_usd=state["max_budget_usd"],
        feedback=state.get("feedback"),
    )
    log.info("coding node produced status=%s", getattr(result, "status", "?"))
    rd = _result_to_dict(result)
    if is_git:
        # .git integrity guard FIRST — BEFORE any further git invocation: if a command
        # tampered with `.git`, neutralise it (restore prior bytes / remove the injected
        # file) and flag it, so our own `git diff` below runs against a clean, un-poisoned
        # config and the family refuses to finalize a tampering run.
        tampered = workspace.git_security_diff(git_before, workspace.git_security_snapshot(ws))
        if tampered:
            workspace.git_security_restore(ws, git_before)
            rd = {**rd, "git_tampered": tampered}
            log.warning("coding node: .git tampering neutralised: %s", tampered)
        # git-aware diff (Phase 10b-1): when the workspace IS a git repo, the authoritative
        # diff + changed files come from git (real, .gitignore-aware); a non-git workspace
        # keeps the seam's snapshot diff (10a). Computed HERE (not in the faked seam) so it
        # rides every path — incl. the offline fake — and is in the review payload below.
        if rd.get("status") in ("completed", "stopped_limit"):
            diff, changed = workspace.git_diff(ws)
            rd = {**rd, "diff": diff, "changed_files": changed}
    # Clear feedback once consumed so a later auto-step doesn't re-apply it.
    return {"result": rd, "feedback": None}


def _finalize_gate_node(state: _State) -> dict:
    # No-op convergence point for "we have a result". The human-review gate branches
    # off here; the non-review default passes straight to END.
    return {}


def _human_review_node(state: _State) -> dict:
    """Human diff-review gate (U2), reusing the Phase 8 interrupt/resume mechanism.

    PURE before interrupt(): build the review payload from state only (LangGraph
    re-runs this node from the top on resume, so nothing before interrupt() may have
    side effects). The payload carries the CodingResult (summary + diff + changed_files
    + status) plus the per-run `task` (Phase 10b-1), the same shape the web RunDetail
    renders.
    """
    payload = {"coding": {**(state["result"] or {}), "task": state.get("task", "")}}
    decision = interrupt(payload)
    # --- resumed via Command(resume=decision) ---
    action = decision.get("action") if isinstance(decision, dict) else decision
    if action == "approve":
        return {"approved": True}
    # redo: a git workspace is RESTORED to its committed state first (Phase 10b-1), so
    # the re-run starts clean and its diff is the new attempt's alone; a non-git
    # workspace re-runs in place (10a). Then re-run a fresh bounded loop with feedback.
    if workspace.is_git_repo(state["workspace"]):
        workspace.git_restore(state["workspace"])
    text = decision.get("feedback") if isinstance(decision, dict) else None
    return {"approved": False, "result": None, "feedback": text}


def _route_after_finalize_gate(state: _State) -> str:
    # Route to the human gate when review is ON and there is reviewable work — a
    # `completed` OR a bounded `stopped_limit` run (the human inspects the partial diff;
    # hardening #3). A hard `failed` seam result skips review (nothing to approve).
    result = state.get("result") or {}
    if (
        state.get("review")
        and not state.get("approved")
        and result.get("status") in ("completed", "stopped_limit")
    ):
        return "human_review"
    return "end"


def _route_after_human_review(state: _State) -> str:
    if state.get("approved"):
        return "end"
    return "coding"  # human asked for a redo → fresh bounded coding loop with feedback


# --- the local registries (NOT components.*: coding is a worker-side island) -
_NODE_HANDLERS: dict = {
    "coding_run": _coding_node,
    "coding_finalize_gate": _finalize_gate_node,
    "coding_human_review": _human_review_node,
}
_PREDICATES: dict = {
    "coding_route_after_finalize_gate": _route_after_finalize_gate,
    "coding_route_after_human_review": _route_after_human_review,
}


def _builder_for(wf: WorkflowDef | None):
    """The graph builder: the prebuilt module builder for the code default (wf is None),
    else a fresh build from a passed def using the LOCAL coding registries. The coding
    family is never web-synthesized, so in practice wf is always None — the override path
    exists only for symmetry with digest/brief and tests."""
    if wf is None:
        return _BUILDER
    return engine.build_graph(wf, _State, node_handlers=_NODE_HANDLERS, predicates=_PREDICATES)


_BUILDER = engine.build_graph(
    CODING_DEF, _State, node_handlers=_NODE_HANDLERS, predicates=_PREDICATES
)
_APP = _BUILDER.compile()


def _initial_state(
    task: str,
    workspace: str,
    model: str,
    *,
    max_turns: int,
    max_tool_calls: int,
    max_budget_usd: float | None,
    feedback: str | None = None,
    review: bool = False,
) -> _State:
    return {
        "task": task,
        "workspace": workspace,
        "model": model,
        "feedback": feedback,
        "max_turns": max_turns,
        "max_tool_calls": max_tool_calls,
        "max_budget_usd": max_budget_usd,
        "result": None,
        "review": review,
        "approved": False,
    }


def build_coding(
    task: str,
    workspace: str,
    *,
    model: str,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    max_budget_usd: float | None = DEFAULT_MAX_BUDGET_USD,
    coding_fn: Callable[..., CodingResult] = run_coding_agent,
    wf: WorkflowDef | None = None,
) -> CodingResult:
    """Produce a CodingResult by running one bounded coding-agent loop through the graph.

    Symmetric to build_digest / build_brief. The seam (`coding_fn`) is injectable for a
    model swap / offline fake; callables are kept OUT of the serializable state. Raises
    RuntimeError if the graph somehow yields no result (defensive — the seam always sets
    one).
    """
    app = _APP if wf is None else _builder_for(wf).compile()
    config = {"configurable": {"coding_fn": coding_fn}}
    final = app.invoke(
        _initial_state(
            task, workspace, model,
            max_turns=max_turns, max_tool_calls=max_tool_calls, max_budget_usd=max_budget_usd,
        ),
        config=config,
    )
    result = final.get("result")
    if result is None:
        raise RuntimeError("coding agent produced no result")
    return _result_from_dict(result)


# --- human-in-the-loop: interruptible diff review (Phase 10a, U2) -------------
# Reuses the Phase 8 web-HITL mechanism verbatim (interrupt + checkpointer + a
# decision-on-resume), swapping the payload from a digest candidate to a coding diff.


@dataclass(frozen=True)
class CodingReviewOutcome:
    """Outcome of an interruptible coding run.

    - status="suspended": paused at the diff-review gate; `payload` is the review
      contract {"coding": <CodingResult JSON>} (what the web RunDetail renders). State
      is persisted by the checkpointer under thread_id; resume with resume_coding_review_run.
    - status="completed": `result` is the final, human-approved CodingResult.
    """

    status: str
    payload: dict | None = None
    result: CodingResult | None = None


def _coding_config(thread_id: str, coding_fn) -> dict:
    return {"configurable": {"thread_id": thread_id, "coding_fn": coding_fn}}


def _outcome_from_state(final: dict) -> CodingReviewOutcome:
    interrupts = final.get("__interrupt__")
    if interrupts:
        return CodingReviewOutcome(status="suspended", payload=interrupts[0].value)
    result = final.get("result")
    if result is None:
        raise RuntimeError("coding agent produced no result")
    return CodingReviewOutcome(status="completed", result=_result_from_dict(result))


def start_coding_review_run(
    task: str,
    workspace: str,
    *,
    model: str,
    thread_id: str,
    checkpointer,
    max_turns: int = DEFAULT_MAX_TURNS,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    max_budget_usd: float | None = DEFAULT_MAX_BUDGET_USD,
    coding_fn: Callable[..., CodingResult] = run_coding_agent,
    wf: WorkflowDef | None = None,
) -> CodingReviewOutcome:
    """Run the coding graph with the diff-review gate ON, persisting to `checkpointer`
    under `thread_id`. Returns suspended (paused at the gate, partial/final diff in the
    payload) or completed. Requires a checkpointer (interrupt needs persistence)."""
    app = _builder_for(wf).compile(checkpointer=checkpointer)
    final = app.invoke(
        _initial_state(
            task, workspace, model,
            max_turns=max_turns, max_tool_calls=max_tool_calls, max_budget_usd=max_budget_usd,
            review=True,
        ),
        config=_coding_config(thread_id, coding_fn),
    )
    return _outcome_from_state(final)


def resume_coding_review_run(
    *,
    thread_id: str,
    checkpointer,
    decision: dict,
    coding_fn: Callable[..., CodingResult] = run_coding_agent,
    wf: WorkflowDef | None = None,
) -> CodingReviewOutcome:
    """Resume a suspended coding run, injecting `decision` into the waiting interrupt()
    (e.g. {"action": "approve"} or {"action": "redo", "feedback": "..."}). Resume is a
    SEPARATE process, so the config (seam + thread_id) is RE-INJECTED — callables are
    never persisted. A redo re-runs a fresh bounded loop and re-presents (suspended
    again); approve completes."""
    app = _builder_for(wf).compile(checkpointer=checkpointer)
    final = app.invoke(Command(resume=decision), config=_coding_config(thread_id, coding_fn))
    return _outcome_from_state(final)
