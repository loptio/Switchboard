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
from dataclasses import asdict
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

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
        log.warning(
            "verification inconclusive (attempt %d); accepting "
            "summarizer-validated digest",
            attempt,
        )
        return {"result": state["digest"]}
    if critique.passed:
        log.info("digest accepted on attempt %d", attempt)
        return {"result": state["digest"]}
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
    return {"result": state["digest"]}


def _finalize_gate_node(state: _State) -> dict:
    # No-op convergence point for every "we have a result" terminal. Unit 3 makes
    # this the optional human-review gate; for the digest default it passes straight
    # to END.
    return {}


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


def _build_builder() -> StateGraph:
    """The single shared graph definition. Compiled without a checkpointer for the
    digest default (`_APP`), and with a checkpointer for interruptible runs."""
    g = StateGraph(_State)
    g.add_node("summarize", _summarize_node)
    g.add_node("verify", _verify_node)
    g.add_node("accept_last", _accept_last_node)
    g.add_node("finalize_gate", _finalize_gate_node)
    g.add_edge(START, "summarize")
    g.add_conditional_edges(
        "summarize",
        _route_after_summarize,
        {
            "verify": "verify",
            "summarize": "summarize",
            "accept_last": "accept_last",
            "give_up": END,
        },
    )
    g.add_conditional_edges(
        "verify",
        _route_after_verify,
        {"finalize_gate": "finalize_gate", "summarize": "summarize", "accept_last": "accept_last"},
    )
    g.add_edge("accept_last", "finalize_gate")
    g.add_edge("finalize_gate", END)
    return g


_APP = _build_builder().compile()  # no checkpointer: the digest default runs straight through


def _initial_state(items: list[FeedItem], n: int, model: str, max_redos: int) -> _State:
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
) -> Digest:
    """Produce a verified Digest via a bounded summarize → verify → redo loop.

    Drop-in for `agent.summarize` from the runner's perspective: same
    (items, n, model) → Digest contract. Internally invokes the LangGraph app
    (no checkpointer); see the module docstring for the control flow.
    """
    if not items:
        return Digest(items=[])  # short-circuit: no model/graph work for empty input

    config = {
        "configurable": {"summarize_fn": summarize_fn, "verify_fn": verify_fn},
        "recursion_limit": _recursion_limit(max_redos),
    }
    final = _APP.invoke(_initial_state(items, n, model, max_redos), config=config)
    if final["result"] is None:
        # The give-up terminal: the summarizer never produced a schema-valid
        # digest within budget. Raise (the run is recorded failed) — no dirty data.
        raise RuntimeError(
            "summarizer never produced a schema-valid digest after "
            f"{max_redos + 1} attempt(s)"
        )
    return _digest_from_dict(final["result"])
