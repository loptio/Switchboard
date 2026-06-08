"""Offline tests for the generic engine (Phase 7, Unit 2) — control primitives.

These exercise engine.build_graph in ISOLATION on a tiny self-contained WorkflowDef
(no agents, no SDK): linear `step`, `conditional` branch, bounded `loop` via a
back-edge, the END sentinel, and clear errors for bad references. The digest's own
tests prove the engine reproduces a real hand-written graph; these pin the
primitives directly.
"""

from typing import TypedDict

import pytest

import engine
from workflows import END, Branch, Node, WorkflowDef


class _State(TypedDict, total=False):
    count: int
    trace: list
    label: str


def _compile(wf, handlers, predicates):
    return engine.build_graph(
        wf, _State, node_handlers=handlers, predicates=predicates
    ).compile()


def test_linear_steps_thread_state():
    def a(state):
        return {"trace": state.get("trace", []) + ["a"]}

    def b(state):
        return {"trace": state["trace"] + ["b"]}

    wf = WorkflowDef(
        id="lin", entry="a",
        nodes=(
            Node("a", "step", handler_ref="a", next="b"),
            Node("b", "step", handler_ref="b", next=END),
        ),
    )
    app = _compile(wf, {"a": a, "b": b}, {})
    out = app.invoke({"trace": []})
    assert out["trace"] == ["a", "b"]


def test_conditional_branch_picks_target_by_label():
    def start(state):
        return {}

    def left(state):
        return {"label": "left"}

    def right(state):
        return {"label": "right"}

    wf = WorkflowDef(
        id="cond", entry="start",
        nodes=(
            Node("start", "step", handler_ref="start",
                 branch=Branch("pick", {"L": "left", "R": "right"})),
            Node("left", "step", handler_ref="left", next=END),
            Node("right", "step", handler_ref="right", next=END),
        ),
    )
    pred = lambda s: "R" if s["count"] >= 5 else "L"  # noqa: E731
    app = _compile(wf, {"start": start, "left": left, "right": right}, {"pick": pred})
    assert app.invoke({"count": 9})["label"] == "right"
    assert app.invoke({"count": 1})["label"] == "left"


def test_bounded_loop_via_back_edge_terminates():
    # 'work' increments count + records a tick, then a branch loops back to 'work'
    # while count < 3, else routes to 'finish' -> END. The bound lives in the
    # predicate (brief D6): a conditional with a back-edge IS the loop primitive.
    def work(state):
        c = state.get("count", 0) + 1
        return {"count": c, "trace": state.get("trace", []) + [f"work{c}"]}

    def finish(state):
        return {"trace": state["trace"] + ["finish"]}

    wf = WorkflowDef(
        id="loop", entry="work",
        nodes=(
            Node("work", "step", handler_ref="work",
                 branch=Branch("again", {"work": "work", "finish": "finish"})),
            Node("finish", "step", handler_ref="finish", next=END),
        ),
    )
    again = lambda s: "work" if s["count"] < 3 else "finish"  # noqa: E731
    app = _compile(wf, {"work": work, "finish": finish}, {"again": again})
    out = app.invoke({"count": 0, "trace": []})
    assert out["count"] == 3
    assert out["trace"] == ["work1", "work2", "work3", "finish"]


def test_unregistered_handler_raises_clear_error():
    wf = WorkflowDef(id="x", entry="a", nodes=(Node("a", "step", handler_ref="missing", next=END),))
    with pytest.raises(ValueError, match="unregistered handler 'missing'"):
        engine.build_graph(wf, _State, node_handlers={}, predicates={})


def test_unregistered_predicate_raises_clear_error():
    wf = WorkflowDef(
        id="x", entry="a",
        nodes=(Node("a", "step", handler_ref="a", branch=Branch("missing", {"k": END})),),
    )
    with pytest.raises(ValueError, match="unregistered predicate 'missing'"):
        engine.build_graph(wf, _State, node_handlers={"a": lambda s: {}}, predicates={})


def test_node_without_edge_raises():
    wf = WorkflowDef(id="x", entry="a", nodes=(Node("a", "step", handler_ref="a"),))
    with pytest.raises(ValueError, match="neither `next` nor `branch`"):
        engine.build_graph(wf, _State, node_handlers={"a": lambda s: {}}, predicates={})


def test_unknown_node_kind_raises():
    wf = WorkflowDef(id="x", entry="a", nodes=(Node("a", "bogus", next=END),))
    with pytest.raises(ValueError, match="unknown kind 'bogus'"):
        engine.build_graph(wf, _State, node_handlers={}, predicates={})
