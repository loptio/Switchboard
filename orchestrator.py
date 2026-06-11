"""Orchestrator — the deterministic control flow that coordinates the agents.

Plain, deterministic control flow, NOT an LLM deciding the flow (that meta-agent
is a later phase). Implemented as a **LangGraph `StateGraph`** (Phase 5 Unit 2):
the engine is borrowed (graph plumbing), the system — agent composition,
contracts, the bounded-redo policy — is ours. `build_digest` replaces the single
`summarize` call in the runner: a bounded summarize → verify → redo loop that
returns the SAME Digest contract, so the rest of the pipeline (render / store /
email) is untouched.

Graph (nodes + conditional edges):
    START → summarize → verify against the source
      pass            → return the digest
      fail (issues)   → feed the critique back, re-summarize        (≤ max_redos)
      cap reached     → accept the last schema-valid digest + log   (verifier LLMs
                        can be wrong / never satisfied, so the cap is the backstop)
      verifier malformed (after a bounded re-verify) → accept current + log
      summarizer never produced a valid digest → raise (run fails; no dirty data)

Everything is bounded: at most (max_redos+1) summarizer calls and at most
(max_redos+1)×MAX_VERIFY_ATTEMPTS verifier calls. No path loops forever or ships
schema-dirty data.

Human-in-the-loop (Phase 5 Unit 3, OPTIONAL): after the auto-loop produces a
verified digest, an optional `human_review` gate `interrupt()`s and hands the
digest to a human (`start_review_run`); the graph suspends, its state persisted
by a checkpointer under thread_id == run_id. `resume_review_run` injects the
decision — approve (→ finish) or redo+feedback (→ a fresh bounded auto-loop, then
re-present). The digest default runs with the gate OFF (review=False, no
checkpointer) — `build_digest` is unchanged.

**State is JSON-native (dict-state), NOT dataclasses** (Phase 5 Unit 3): the
checkpointer (human-in-the-loop suspend/resume) serializes the state, and
LangGraph's serializer only round-trips arbitrary dataclasses via a deprecated,
schema-brittle path. So the graph state holds plain dicts/lists/primitives;
nodes convert to/from the FeedItem/Digest/Critique dataclasses at their
boundaries. The PUBLIC contract is unchanged: `build_digest` takes FeedItems and
returns a Digest, and the injected agents still speak dataclasses.

The agents are injected (summarize_fn / verify_fn) via LangGraph's per-invoke
`config["configurable"]` so the model can be swapped and tests can inject fakes
without touching this control flow. Callables are kept OUT of the state (it must
serialize). Nodes reach the model ONLY via the injected agents (i.e. llm.py) —
never the SDK directly.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import TypedDict

from langgraph.types import Command, interrupt

import components
import engine
from agent import (
    AgentContractError,
    Critique,
    CritiqueIssue,
    Digest,
    DigestItem,
    summarize_agent,
    verify_agent,
)
from fetch import FeedItem
from workflows import DIGEST_DEF, WorkflowDef

log = logging.getLogger(__name__)

DEFAULT_MAX_REDOS = 2  # 1 initial draft + up to 2 redos (brief §2②)
MAX_VERIFY_ATTEMPTS = 2  # re-verify the SAME digest once if the verifier is malformed

# Fixed, instructional feedback for a malformed summarizer reply. We deliberately
# do NOT feed the raw exception/model output back (the model may just re-emit it,
# and it could carry source text) — a clean instruction is likelier to fix it.
_FORMAT_FEEDBACK = Critique(
    passed=False,
    issues=[
        CritiqueIssue(
            index=None,
            kind="format",
            detail=(
                "Your previous reply was not valid output. Return ONLY a JSON "
                "array with exactly one object per item, in order, each with a "
                'non-empty "one_line_summary".'
            ),
        )
    ],
)


# --- dict-state converters --------------------------------------------------
# `to_dict` is dataclasses.asdict (recurses into nested dataclasses). `from_dict`
# is TOLERANT of missing keys (.get with defaults): that is the whole point of
# dict-state — a checkpoint written by an older build must still resume after a
# redeploy that added a field. Round-trip identity (from_dict∘to_dict == obj) is
# pinned by tests.


def _feeditem_to_dict(item: FeedItem) -> dict:
    return asdict(item)


def _feeditem_from_dict(d: dict) -> FeedItem:
    return FeedItem(
        title=d.get("title", ""),
        link=d.get("link", ""),
        summary=d.get("summary", ""),
        published=d.get("published", ""),
    )


def _digest_to_dict(digest: Digest) -> dict:
    return asdict(digest)


def _digest_from_dict(d: dict) -> Digest:
    return Digest(
        items=[
            DigestItem(
                title=it.get("title", ""),
                link=it.get("link", ""),
                one_line_summary=it.get("one_line_summary", ""),
            )
            for it in d.get("items", [])
        ]
    )


def _critique_to_dict(critique: Critique) -> dict:
    return asdict(critique)


def _critique_from_dict(d: dict) -> Critique:
    return Critique(
        passed=bool(d.get("passed", False)),
        issues=[
            CritiqueIssue(
                index=iss.get("index"),
                kind=iss.get("kind", "unspecified"),
                detail=iss.get("detail", ""),
            )
            for iss in d.get("issues", [])
        ],
    )


def _summarize_issues(critique: Critique | None) -> str:
    """One-line render of a critique's open issues for the cap-reached log."""
    if critique is None or not critique.issues:
        return "(none)"
    return "; ".join(
        f"[{i.index if i.index else 'overall'}/{i.kind}] {i.detail}"
        for i in critique.issues
    )


def _verify_bounded(
    digest: Digest,
    items: list[FeedItem],
    model: str,
    verify_fn: Callable[..., Critique],
) -> Critique | None:
    """Verify a digest, re-verifying the SAME digest if the verifier is malformed.

    Returns the Critique, or None if the verifier never produced a valid critique
    within MAX_VERIFY_ATTEMPTS (inconclusive — the caller degrades gracefully).
    A single transient malformed reply shouldn't silently disable verification.
    """
    for attempt in range(1, MAX_VERIFY_ATTEMPTS + 1):
        try:
            return verify_fn(digest, items, model)
        except AgentContractError as exc:
            log.warning(
                "verifier produced invalid output (attempt %d/%d): %s",
                attempt,
                MAX_VERIFY_ATTEMPTS,
                exc,
            )
    return None


# --- LangGraph state + nodes ------------------------------------------------


class _State(TypedDict):
    """Graph state — JSON-native only (checkpointer-serializable). NO dataclasses
    and NO callables (agents are injected via config)."""

    items: list[dict]  # serialized FeedItems
    n: int
    model: str
    max_redos: int
    attempt: int  # number of summarize calls made so far (1-based after a call)
    feedback: dict | None  # serialized Critique
    digest: dict | None  # serialized Digest — most recent valid candidate (retained)
    summarize_ok: bool
    result: dict | None  # serialized Digest — set only by a terminal node; the answer
    review: bool  # human-in-the-loop gate on? (digest default: False)
    approved: bool  # has the human approved the current digest?
    verdict: str | None  # Phase 11 observability: passed / accepted_at_cap / inconclusive


def _summarize_node(state: _State, config) -> dict:
    summarize_fn = config["configurable"]["summarize_fn"]
    attempt = state["attempt"] + 1
    items = [_feeditem_from_dict(d) for d in state["items"]]
    feedback = _critique_from_dict(state["feedback"]) if state["feedback"] else None
    try:
        digest = summarize_fn(items, state["n"], state["model"], feedback=feedback)
    except AgentContractError as exc:
        # Summarizer output failed its contract — never ship dirty data. Leave
        # `digest` untouched (LangGraph keeps the prior value) so a later cap can
        # still fall back to the last schema-valid digest.
        log.warning("summarizer invalid output (attempt %d): %s", attempt, exc)
        return {
            "attempt": attempt,
            "summarize_ok": False,
            "feedback": _critique_to_dict(_FORMAT_FEEDBACK),
        }
    return {"attempt": attempt, "digest": _digest_to_dict(digest), "summarize_ok": True}


def _verify_node(state: _State, config) -> dict:
    verify_fn = config["configurable"]["verify_fn"]
    digest = _digest_from_dict(state["digest"])
    items = [_feeditem_from_dict(d) for d in state["items"]]
    attempt = state["attempt"]
    critique = _verify_bounded(digest, items, state["model"], verify_fn)
    if critique is None:
        # Verifier couldn't produce a valid review: accept the summarizer-validated
        # digest (degrades to pre-Phase-5 quality, never masks a fail as a pass).
        # Clear feedback: no concrete open issues to carry into a human-review gate.
        log.warning(
            "verification inconclusive (attempt %d); accepting "
            "summarizer-validated digest",
            attempt,
        )
        return {"result": state["digest"], "feedback": None, "verdict": "inconclusive"}
    if critique.passed:
        # Clean pass — clear any stale feedback from earlier redos so a human-review
        # gate shows zero open issues.
        log.info("digest accepted on attempt %d", attempt)
        return {"result": state["digest"], "feedback": None, "verdict": "passed"}
    log.info(
        "digest rejected on attempt %d (%d issue(s)); redoing",
        attempt,
        len(critique.issues),
    )
    return {"feedback": _critique_to_dict(critique)}


def _accept_last_node(state: _State) -> dict:
    # Redo budget exhausted with the digest still failing review: accept the last
    # schema-valid digest and log the open issues (the bounded backstop).
    feedback = _critique_from_dict(state["feedback"]) if state["feedback"] else None
    log.warning(
        "redo limit (%d) reached; accepting last digest with open issues: %s",
        state["max_redos"],
        _summarize_issues(feedback),
    )
    return {"result": state["digest"], "verdict": "accepted_at_cap"}


def _finalize_gate_node(state: _State) -> dict:
    # No-op convergence point for every "we have a result" terminal. The optional
    # human-review gate branches off here; the digest default passes straight to END.
    return {}


def _human_review_node(state: _State) -> dict:
    # PURE before interrupt(): build the review payload from state only — no LLM,
    # no DB write. LangGraph re-runs this node from the top on resume, so anything
    # before interrupt() must be side-effect-free.
    payload = {
        "digest": state["digest"],  # candidate Digest (JSON) — the web-facing contract
        "issues": (state["feedback"] or {}).get("issues", []),  # open critique issues
    }
    decision = interrupt(payload)
    # --- resumed via Command(resume=decision) ---
    action = decision.get("action") if isinstance(decision, dict) else decision
    if action == "approve":
        return {"approved": True}
    # redo: reset for a fresh, fully-bounded auto-loop (attempt=0) carrying the
    # human's feedback. Human redos are human-driven, not counted against max_redos.
    text = decision.get("feedback") if isinstance(decision, dict) else None
    feedback = (
        _critique_to_dict(
            Critique(
                passed=False,
                issues=[CritiqueIssue(index=None, kind="human", detail=text)],
            )
        )
        if text
        else None
    )
    return {"approved": False, "result": None, "attempt": 0, "feedback": feedback}


def _route_after_summarize(state: _State) -> str:
    if state["summarize_ok"]:
        return "verify"
    # dirty summarizer output
    if state["attempt"] <= state["max_redos"]:
        return "summarize"  # redo (feedback already set to _FORMAT_FEEDBACK)
    if state["digest"] is not None:
        return "accept_last"
    return "give_up"  # no valid digest ever -> END with result=None -> RuntimeError


def _route_after_verify(state: _State) -> str:
    if state["result"] is not None:
        return "finalize_gate"  # pass or inconclusive already set the result
    # failing critique (feedback already set to the critique)
    if state["attempt"] <= state["max_redos"]:
        return "summarize"
    return "accept_last"


def _route_after_finalize_gate(state: _State) -> str:
    # The optional human-review gate. digest default (review off) → straight to END.
    if state.get("review") and not state.get("approved"):
        return "human_review"
    return "end"


def _route_after_human_review(state: _State) -> str:
    if state.get("approved"):
        return "end"
    return "summarize"  # human asked for a redo → fresh auto-loop with their feedback


# Digest graph glue (Phase 7, Unit 2): the node handlers + routing predicates,
# registered BY NAME into the component registry so the GENERIC engine
# (engine.build_graph) can wire the DIGEST_DEF topology (workflows.py) from data.
# This replaces the former hand-written `_build_builder()`: same nodes, same
# handlers, same conditional edges / bounded redo loop / review gate — so the
# existing digest tests (test_orchestrator, test_human_in_the_loop) are the
# "generic engine behaviour == hand-written graph" no-regression proof.
_NODE_HANDLERS = {
    "digest_summarize": _summarize_node,
    "digest_verify": _verify_node,
    "digest_accept_last": _accept_last_node,
    "digest_finalize_gate": _finalize_gate_node,
    "digest_human_review": _human_review_node,
}
_PREDICATES = {
    "digest_route_after_summarize": _route_after_summarize,
    "digest_route_after_verify": _route_after_verify,
    "digest_route_after_finalize_gate": _route_after_finalize_gate,
    "digest_route_after_human_review": _route_after_human_review,
}
for _name, _fn in _NODE_HANDLERS.items():
    components.register(components.NODE_HANDLERS, _name, _fn)
for _name, _fn in _PREDICATES.items():
    components.register(components.PREDICATES, _name, _fn)

# One shared builder (the system's graph), now COMPILED FROM the digest WorkflowDef
# by the generic engine, compiled two ways: without a checkpointer for the
# straight-through digest default (`_APP`), and — per interruptible run — WITH an
# injected checkpointer for human-in-the-loop suspend/resume.
_BUILDER = engine.build_graph(
    DIGEST_DEF, _State, node_handlers=_NODE_HANDLERS, predicates=_PREDICATES
)
_APP = _BUILDER.compile()  # no checkpointer: the digest default runs straight through


def _builder_for(wf: WorkflowDef | None):
    """The graph builder for a run: the prebuilt module builder for the code default
    (wf is None — byte-for-byte the pre-Phase-8 path), else a fresh builder compiled
    from a DB-resolved WorkflowDef using the FULL component registries (a user def may
    reference any registered handler/predicate/composer). This is the load-time guard
    (#2): a bad ref or broken topology raises here (engine.build_graph / .compile)."""
    if wf is None:
        return _BUILDER
    return engine.build_graph(
        wf,
        _State,
        node_handlers=components.NODE_HANDLERS,
        predicates=components.PREDICATES,
        composers=components.COMPOSERS,
    )


def _initial_state(
    items: list[FeedItem], n: int, model: str, max_redos: int, *, review: bool = False
) -> _State:
    return {
        "items": [_feeditem_to_dict(it) for it in items],
        "n": n,
        "model": model,
        "max_redos": max_redos,
        "attempt": 0,
        "feedback": None,
        "digest": None,
        "summarize_ok": False,
        "result": None,
        "review": review,
        "approved": False,
        "verdict": None,
    }


def _recursion_limit(max_redos: int) -> int:
    # Bound the engine's own loop generously relative to our redo cap so a custom
    # max_redos never trips LangGraph's recursion guard (max_redos is the real
    # bound). Each attempt is ~2 super-steps (summarize + verify).
    return 2 * (max_redos + 1) + 10


def build_digest(
    items: list[FeedItem],
    n: int,
    model: str,
    *,
    max_redos: int = DEFAULT_MAX_REDOS,
    summarize_fn: Callable[..., Digest] = summarize_agent,
    verify_fn: Callable[..., Critique] = verify_agent,
    wf: WorkflowDef | None = None,
) -> Digest:
    """Produce a verified Digest via a bounded summarize → verify → redo loop.

    Drop-in for `agent.summarize` from the runner's perspective: same
    (items, n, model) → Digest contract. Internally invokes the LangGraph app
    (no checkpointer); see the module docstring for the control flow.
    """
    digest, _verdict = build_digest_with_verdict(
        items, n, model,
        max_redos=max_redos, summarize_fn=summarize_fn, verify_fn=verify_fn, wf=wf,
    )
    return digest


def build_digest_with_verdict(
    items: list[FeedItem],
    n: int,
    model: str,
    *,
    max_redos: int = DEFAULT_MAX_REDOS,
    summarize_fn: Callable[..., Digest] = summarize_agent,
    verify_fn: Callable[..., Critique] = verify_agent,
    wf: WorkflowDef | None = None,
) -> tuple[Digest, str | None]:
    """Like `build_digest`, but also returns the review VERDICT (Phase 11
    observability): 'passed' (verifier approved), 'accepted_at_cap' (redo budget
    exhausted, accepted the last valid digest with open issues), or 'inconclusive'
    (the verifier never produced a valid review). None for the empty-input
    short-circuit. The runner persists this onto the Run so the UI can show digest
    *quality*, not just success/failed. `build_digest` delegates here and drops it,
    so its (items, n, model) → Digest contract — and every test of it — is unchanged."""
    if not items:
        return Digest(items=[]), None  # short-circuit: no model/graph work

    app = _APP if wf is None else _builder_for(wf).compile()
    config = {
        "configurable": {"summarize_fn": summarize_fn, "verify_fn": verify_fn},
        "recursion_limit": _recursion_limit(max_redos),
    }
    final = app.invoke(_initial_state(items, n, model, max_redos), config=config)
    if final["result"] is None:
        # The give-up terminal: the summarizer never produced a schema-valid
        # digest within budget. Raise (the run is recorded failed) — no dirty data.
        raise RuntimeError(
            "summarizer never produced a schema-valid digest after "
            f"{max_redos + 1} attempt(s)"
        )
    return _digest_from_dict(final["result"]), final.get("verdict")


# --- human-in-the-loop: interruptible runs (Phase 5 Unit 3) -----------------


@dataclass(frozen=True)
class ReviewOutcome:
    """Outcome of an interruptible run.

    - status="suspended": paused at the human-review gate; `payload` is the
      review contract {"digest": <Digest JSON>, "issues": [<critique issue JSON>]}
      (the same shape a future web UI will render). State is persisted by the
      checkpointer under the thread_id; resume with `resume_review_run`.
    - status="completed": `digest` is the final, human-approved Digest.
    """

    status: str
    payload: dict | None = None
    digest: Digest | None = None


def _outcome_from_state(final: dict, max_redos: int) -> ReviewOutcome:
    interrupts = final.get("__interrupt__")
    if interrupts:
        return ReviewOutcome(status="suspended", payload=interrupts[0].value)
    if final.get("result") is None:
        raise RuntimeError(
            "summarizer never produced a schema-valid digest after "
            f"{max_redos + 1} attempt(s)"
        )
    return ReviewOutcome(status="completed", digest=_digest_from_dict(final["result"]))


def start_review_run(
    items: list[FeedItem],
    n: int,
    model: str,
    *,
    thread_id: str,
    checkpointer,
    max_redos: int = DEFAULT_MAX_REDOS,
    summarize_fn: Callable[..., Digest] = summarize_agent,
    verify_fn: Callable[..., Critique] = verify_agent,
    wf: WorkflowDef | None = None,
) -> ReviewOutcome:
    """Run the graph with the human-review gate ON, persisting to `checkpointer`
    under `thread_id`. Returns suspended (paused at the gate) or completed.

    The auto summarize→verify→redo loop runs first; the human reviews the final,
    already-verified digest. Requires a checkpointer (interrupt needs persistence).
    """
    if not items:
        return ReviewOutcome(status="completed", digest=Digest(items=[]))
    app = _builder_for(wf).compile(checkpointer=checkpointer)
    config = {
        "configurable": {
            "thread_id": thread_id,
            "summarize_fn": summarize_fn,
            "verify_fn": verify_fn,
        },
        "recursion_limit": _recursion_limit(max_redos),
    }
    final = app.invoke(_initial_state(items, n, model, max_redos, review=True), config=config)
    return _outcome_from_state(final, max_redos)


def resume_review_run(
    *,
    thread_id: str,
    checkpointer,
    decision: dict,
    max_redos: int = DEFAULT_MAX_REDOS,
    summarize_fn: Callable[..., Digest] = summarize_agent,
    verify_fn: Callable[..., Critique] = verify_agent,
    wf: WorkflowDef | None = None,
) -> ReviewOutcome:
    """Resume a suspended run from its checkpoint, injecting `decision` into the
    waiting interrupt() (e.g. {"action": "approve"} or
    {"action": "redo", "feedback": "..."}).

    Resume is a SEPARATE process, so the config (agents + thread_id) is RE-INJECTED
    — callables are never persisted in the checkpoint. A human "redo" re-runs a
    fresh bounded auto-loop and re-presents (status="suspended" again); "approve"
    completes (status="completed").
    """
    app = _builder_for(wf).compile(checkpointer=checkpointer)
    config = {
        "configurable": {
            "thread_id": thread_id,
            "summarize_fn": summarize_fn,
            "verify_fn": verify_fn,
        },
        "recursion_limit": _recursion_limit(max_redos),
    }
    final = app.invoke(Command(resume=decision), config=config)
    return _outcome_from_state(final, max_redos)
