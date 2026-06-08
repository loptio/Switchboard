"""Offline tests for the fan_out / gather primitives (Phase 7, Unit 3).

Exercises engine_fanout in ISOLATION on a tiny self-contained nested fan_out (no
agents): the key property is DETERMINISTIC, ORDER-PRESERVING execution (brief D4) —
item-major, head-before-leaves, inner order preserved — which is exactly what keeps
the brief's pinned call-order test green. Plus gather assembly, empty input, and
build-time errors for bad references.
"""

from typing import TypedDict

import pytest

import engine
from workflows import END, Node, WorkflowDef


class _State(TypedDict, total=False):
    items: list
    parts: list
    built: list
    result: dict


# A mini "brief": items ⊃ parts (mirrors kept ⊃ stances). `calls` records the exact
# invocation order across both levels.
def _mini_workflow():
    return WorkflowDef(
        id="mini", entry="build",
        nodes=(
            Node(
                "build", "fan_out",
                over="items", element_key="item", collect_ref="mk_item", into="built",
                body=(
                    Node("head", "step", handler_ref="head"),
                    Node(
                        "subparts", "fan_out",
                        over="parts", element_key="part",
                        collect_ref="mk_part", into="subs",
                        body=(Node("leaf", "step", handler_ref="leaf"),),
                    ),
                ),
                next="done",
            ),
            Node("done", "gather", compose_ref="wrap", into="result", next=END),
        ),
    )


def _make_glue(calls):
    def head(sub, config):
        calls.append(("head", sub["item"]))
        return {"head": "H:" + sub["item"]}

    def leaf(sub, config):
        calls.append(("leaf", sub["item"], sub["part"]))
        return {"leaf": f"{sub['part']}:{sub['item']}"}

    handlers = {"head": head, "leaf": leaf}
    composers = {
        "mk_part": lambda sub: sub["leaf"],
        "mk_item": lambda sub: {"item": sub["item"], "head": sub["head"], "subs": sub["subs"]},
        "wrap": lambda state: {"all": state["built"]},
    }
    return handlers, composers


def _compile(wf, handlers, composers):
    return engine.build_graph(
        wf, _State, node_handlers=handlers, predicates={}, composers=composers
    ).compile()


def test_nested_fan_out_preserves_item_major_then_inner_order():
    calls = []
    handlers, composers = _make_glue(calls)
    app = _compile(_mini_workflow(), handlers, composers)

    out = app.invoke({"items": ["A", "B"], "parts": ["x", "y"]})

    # THE D4 PROPERTY: item-major, head before its leaves, inner order preserved.
    assert calls == [
        ("head", "A"), ("leaf", "A", "x"), ("leaf", "A", "y"),
        ("head", "B"), ("leaf", "B", "x"), ("leaf", "B", "y"),
    ]


def test_gather_assembles_the_collected_results():
    calls = []
    handlers, composers = _make_glue(calls)
    app = _compile(_mini_workflow(), handlers, composers)

    out = app.invoke({"items": ["A", "B"], "parts": ["x", "y"]})

    assert out["result"] == {
        "all": [
            {"item": "A", "head": "H:A", "subs": ["x:A", "y:A"]},
            {"item": "B", "head": "H:B", "subs": ["x:B", "y:B"]},
        ]
    }


def test_empty_over_yields_empty_collection_no_body_calls():
    calls = []
    handlers, composers = _make_glue(calls)
    app = _compile(_mini_workflow(), handlers, composers)

    out = app.invoke({"items": [], "parts": ["x"]})

    assert out["result"] == {"all": []}
    assert calls == []  # no element -> body never runs


def test_fan_out_unregistered_composer_raises_at_build():
    wf = WorkflowDef(
        id="x", entry="f",
        nodes=(
            Node("f", "fan_out", over="items", element_key="i",
                 collect_ref="missing", into="out",
                 body=(Node("s", "step", handler_ref="s"),), next=END),
        ),
    )
    with pytest.raises(ValueError, match="unregistered composer 'missing'"):
        engine.build_graph(
            wf, _State, node_handlers={"s": lambda sub, c: {}}, predicates={}, composers={}
        )


def test_fan_out_unregistered_body_handler_raises_at_build():
    wf = WorkflowDef(
        id="x", entry="f",
        nodes=(
            Node("f", "fan_out", over="items", element_key="i", into="out",
                 body=(Node("s", "step", handler_ref="missing"),), next=END),
        ),
    )
    with pytest.raises(ValueError, match="unregistered handler 'missing'"):
        engine.build_graph(wf, _State, node_handlers={}, predicates={}, composers={})


def test_gather_unregistered_composer_raises_at_build():
    wf = WorkflowDef(
        id="x", entry="g",
        nodes=(Node("g", "gather", compose_ref="missing", into="result", next=END),),
    )
    with pytest.raises(ValueError, match="unregistered composer 'missing'"):
        engine.build_graph(wf, _State, node_handlers={}, predicates={}, composers={})
