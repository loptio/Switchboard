"""Brief orchestrator — the brief workflow, now run by the GENERIC engine (Phase 7).

`build_brief` is unchanged from the caller's view (gathered SourceItems in, a `Brief`
out), but its graph is no longer hand-written: it is the `BRIEF_DEF` WorkflowDef
(workflows.py) compiled by `engine.build_graph`. The former hand-written
filter→compose→END graph is gone; the node behaviour lives in handlers/composers
registered BY NAME into the component registry, which the engine wires from data.

Graph (compiled from BRIEF_DEF):
    START → filter → compose → assemble → END
      filter  : keep the valuable items (<= keep_cap)             [filter_agent]   (step)
      compose : per kept item, one summary + one take per stance, [summarize/      (fan_out
                assembled into a BriefItem                         perspective]      + nested fan_out)
      assemble: wrap the BriefItems into the Brief contract                         (gather)

The fan-out is executed as a DETERMINISTIC, order-preserving sequential map inside
the `compose` node (engine_fanout) — byte-for-byte the call order of the old
`_compose_node` (brief D4): per kept item, the summary first, then one perspective
per stance in order. That fidelity is what keeps the existing brief tests green (the
"generic engine == hand-written graph" no-regression proof).

State is JSON-native (dict-state), matching the digest: handlers convert to/from the
SourceItem/Brief dataclasses at their boundaries; the agents (injected via
`config["configurable"]`) keep speaking dataclasses. No checkpointer / no
human-in-the-loop gate (brief §11).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from typing import TypedDict

import components
import engine
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
from workflows import BRIEF_DEF, WorkflowDef

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


# --- LangGraph state --------------------------------------------------------
class _State(TypedDict):
    items: list[dict]      # serialized SourceItems (gathered)
    stances: list[str]
    keep_cap: int
    model: str
    date: str
    kept: list[dict] | None        # serialized SourceItems kept by the filter
    brief_items: list[dict] | None  # serialized BriefItems from the fan_out
    result: dict | None             # serialized Brief — the answer


# --- node handlers (step behaviour: state/sub-state <-> agent <-> delta) -----
# `filter` runs on the graph state; `summary`/`perspective` run on a per-element
# SUB-STATE the fan_out builds (the current item under "item", the current stance
# under "stance"), inheriting "model" from the parent state. All keep dict-state.


def _filter_node(state, config) -> dict:
    filter_fn = config["configurable"]["filter_fn"]
    items = [_sourceitem_from_dict(d) for d in state["items"]]
    kept = _bounded(
        lambda: filter_fn(items, state["model"], keep_cap=state["keep_cap"]), "filter"
    )
    # Enforce the cost cap here too, so it holds even if a filter agent over-returns.
    kept = kept[: state["keep_cap"]]
    log.info("filter kept %d/%d item(s)", len(kept), len(items))
    return {"kept": [asdict(k) for k in kept]}


def _summary_node(sub, config) -> dict:
    summarize_fn = config["configurable"]["summarize_fn"]
    item = _sourceitem_from_dict(sub["item"])
    label = item.title[:40]
    summary = _bounded(lambda: summarize_fn(item, sub["model"]), f"summary[{label}]")
    return {"summary": summary}


def _perspective_node(sub, config) -> dict:
    perspective_fn = config["configurable"]["perspective_fn"]
    item = _sourceitem_from_dict(sub["item"])
    stance = sub["stance"]
    label = item.title[:40]
    take = _bounded(
        lambda: perspective_fn(item, stance, sub["model"]),
        f"{stance} perspective[{label}]",
    )
    return {"perspective": asdict(take)}  # Perspective -> dict (JSON-native state)


# --- composers (assembly: sub-state / state -> a contract value) ------------
def _perspective_value(sub) -> dict:
    # the per-stance fan_out's element value: the Perspective dict the step wrote.
    return sub["perspective"]


def _brief_item(sub) -> dict:
    # one kept item's BriefItem: provenance (title/link/source/domain) straight from
    # the source item — never the model; summary + perspectives from the agents.
    item = sub["item"]
    return {
        "title": item["title"],
        "link": item["link"],
        "source": item["source"],
        "domain": item["domain"],
        "summary": sub["summary"],
        "perspectives": sub["perspectives"],
    }


def _assemble_brief(state) -> dict:
    # gather: wrap the collected BriefItems into the Brief contract.
    return {"date": state["date"], "items": state["brief_items"] or []}


# --- register the glue by name + compile BRIEF_DEF via the generic engine ----
_NODE_HANDLERS = {
    "brief_filter": _filter_node,
    "brief_summary": _summary_node,
    "brief_perspective": _perspective_node,
}
_COMPOSERS = {
    "perspective_value": _perspective_value,
    "brief_item": _brief_item,
    "assemble_brief": _assemble_brief,
}
for _name, _fn in _NODE_HANDLERS.items():
    components.register(components.NODE_HANDLERS, _name, _fn)
for _name, _fn in _COMPOSERS.items():
    components.register(components.COMPOSERS, _name, _fn)

# The brief graph, compiled from BRIEF_DEF by the generic engine (no predicates —
# the brief has no conditional edges; the fan_out/gather use composers).
_APP = engine.build_graph(
    BRIEF_DEF, _State, node_handlers=_NODE_HANDLERS, predicates={}, composers=_COMPOSERS
).compile()


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
        "brief_items": None,
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
    wf: WorkflowDef | None = None,
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
    # wf is None (code default) -> the prebuilt module graph (byte-for-byte); a
    # DB-resolved override compiles fresh via the FULL registries (load-time guard #2).
    app = (
        _APP
        if wf is None
        else engine.build_graph(
            wf,
            _State,
            node_handlers=components.NODE_HANDLERS,
            predicates=components.PREDICATES,
            composers=components.COMPOSERS,
        ).compile()
    )
    final = app.invoke(
        _initial_state(items, model, list(stances), keep_cap, date_str), config=config
    )
    return _brief_from_dict(final["result"])
