"""Brief orchestrator — deterministic control flow for the brief workflow.

Like the digest's `orchestrator.build_digest`, this is plain, deterministic control
flow implemented as a **LangGraph `StateGraph`** (the engine is borrowed; the
composition is ours). `build_brief` is the workflow entry point, symmetric to
`build_digest`: gathered SourceItems in, a `Brief` out.

Graph (a linear pipeline):
    START → filter → compose → END
      filter : keep the valuable items (<= keep_cap)            [filter_agent]
      compose: per kept item, one summary + one take per stance [summarize/perspective]
               then assemble the Brief

The fan-out that makes this a multi-agent job lives in `compose`: each kept item
gets one summary call plus N (default 3) perspective calls, each perspective a
SEPARATE prompt with fresh, un-anchored context.

Bounds (brief §3 cost gate): the collection layer caps items per source (<=20);
the filter caps kept items (<=8); each kept item makes 1 + len(stances) calls. With
the defaults that is at most 1 (filter) + 8×(1 + 3) = 33 model calls. There is NO
verifier and NO redo loop (the digest has those; the brief enforces faithfulness by
prompt + non-rewritable provenance instead). A persistently malformed agent reply
is retried a bounded number of times, then fails the run (no dirty data).

State is JSON-native (dict-state), matching the digest orchestrator, so nodes
convert to/from the SourceItem/Brief dataclasses at their boundaries and the agents
(injected via `config["configurable"]`) keep speaking dataclasses. The brief runs
with NO checkpointer and NO human-in-the-loop gate by default (brief §11).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from agent import AgentContractError
from brief_agent import (
    KEEP_CAP,
    STANCES,
    Brief,
    BriefItem,
    Perspective,
    filter_agent,
    perspective_agent,
    summarize_item_agent,
)
from sources import SourceItem

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # retry an agent whose reply is malformed, then fail the run


# --- dict-state converters --------------------------------------------------
def _sourceitem_from_dict(d: dict) -> SourceItem:
    return SourceItem(
        title=d.get("title", ""),
        link=d.get("link", ""),
        source=d.get("source", ""),
        domain=d.get("domain", ""),
        published=d.get("published", ""),
        text=d.get("text", ""),
    )


def _brief_to_dict(brief: Brief) -> dict:
    return asdict(brief)


def _brief_from_dict(d: dict) -> Brief:
    return Brief(
        date=d.get("date", ""),
        items=[
            BriefItem(
                title=it.get("title", ""),
                link=it.get("link", ""),
                source=it.get("source", ""),
                domain=it.get("domain", ""),
                summary=it.get("summary", ""),
                perspectives=[
                    Perspective(stance=p.get("stance", ""), take=p.get("take", ""))
                    for p in it.get("perspectives", [])
                ],
            )
            for it in d.get("items", [])
        ],
    )


def _bounded(call: Callable[[], object], what: str):
    """Call an agent, retrying a malformed (AgentContractError) reply up to
    MAX_ATTEMPTS, then raising RuntimeError. Keeps one transient bad reply from
    failing the run while staying strictly bounded (no dirty data ever ships)."""
    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return call()
        except AgentContractError as exc:
            last = exc
            log.warning(
                "%s produced invalid output (attempt %d/%d): %s",
                what, attempt, MAX_ATTEMPTS, exc,
            )
    raise RuntimeError(
        f"{what} did not produce valid output after {MAX_ATTEMPTS} attempts: {last}"
    )


# --- LangGraph state + nodes ------------------------------------------------
class _State(TypedDict):
    items: list[dict]      # serialized SourceItems (gathered)
    stances: list[str]
    keep_cap: int
    model: str
    date: str
    kept: list[dict] | None    # serialized SourceItems kept by the filter
    result: dict | None        # serialized Brief — the answer


def _filter_node(state: _State, config) -> dict:
    filter_fn = config["configurable"]["filter_fn"]
    items = [_sourceitem_from_dict(d) for d in state["items"]]
    kept = _bounded(
        lambda: filter_fn(items, state["model"], keep_cap=state["keep_cap"]), "filter"
    )
    # Enforce the cost cap here too, so it holds even if a filter agent over-returns.
    kept = kept[: state["keep_cap"]]
    log.info("filter kept %d/%d item(s)", len(kept), len(items))
    return {"kept": [asdict(k) for k in kept]}


def _compose_node(state: _State, config) -> dict:
    summarize_fn = config["configurable"]["summarize_fn"]
    perspective_fn = config["configurable"]["perspective_fn"]
    kept = [_sourceitem_from_dict(d) for d in state["kept"] or []]
    brief_items: list[BriefItem] = []
    for item in kept:
        label = item.title[:40]
        summary = _bounded(
            lambda item=item: summarize_fn(item, state["model"]), f"summary[{label}]"
        )
        perspectives: list[Perspective] = []
        for stance in state["stances"]:
            take = _bounded(
                lambda item=item, stance=stance: perspective_fn(item, stance, state["model"]),
                f"{stance} perspective[{label}]",
            )
            perspectives.append(take)
        # title/link/source/domain straight from the source item — never the model.
        brief_items.append(
            BriefItem(
                title=item.title,
                link=item.link,
                source=item.source,
                domain=item.domain,
                summary=summary,
                perspectives=perspectives,
            )
        )
    brief = Brief(date=state["date"], items=brief_items)
    return {"result": _brief_to_dict(brief)}


def _build_app():
    g = StateGraph(_State)
    g.add_node("filter", _filter_node)
    g.add_node("compose", _compose_node)
    g.add_edge(START, "filter")
    g.add_edge("filter", "compose")
    g.add_edge("compose", END)
    return g.compile()


_APP = _build_app()


def _initial_state(
    items: list[SourceItem], model: str, stances: list[str], keep_cap: int, date_str: str
) -> _State:
    return {
        "items": [asdict(it) for it in items],
        "stances": stances,
        "keep_cap": keep_cap,
        "model": model,
        "date": date_str,
        "kept": None,
        "result": None,
    }


def build_brief(
    items: list[SourceItem],
    *,
    model: str,
    day: date,
    stances: tuple[str, ...] | list[str] = STANCES,
    keep_cap: int = KEEP_CAP,
    filter_fn: Callable[..., list[SourceItem]] = filter_agent,
    summarize_fn: Callable[..., str] = summarize_item_agent,
    perspective_fn: Callable[..., Perspective] = perspective_agent,
) -> Brief:
    """Produce a Brief from gathered SourceItems: filter → per-item summary + N
    perspectives → assemble. Symmetric to `build_digest`.

    `day` dates the Brief. The agents are injectable (model swap / offline fakes)
    via LangGraph's per-invoke config; callables are kept OUT of the serializable
    state. Empty input short-circuits to an empty Brief (no model/graph work).
    """
    date_str = day.isoformat()
    if not items:
        return Brief(date=date_str, items=[])
    config = {
        "configurable": {
            "filter_fn": filter_fn,
            "summarize_fn": summarize_fn,
            "perspective_fn": perspective_fn,
        }
    }
    final = _APP.invoke(
        _initial_state(items, model, list(stances), keep_cap, date_str), config=config
    )
    return _brief_from_dict(final["result"])
