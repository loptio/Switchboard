"""Offline tests for the coding family (Phase 10a) — fake seam, NO SDK.

build_coding compiles CODING_DEF via the GENERIC engine and runs the bounded coding
node by calling the injected seam. A deterministic fake stands in for the seam, so the
whole family runs offline (no SDK, no key, no spend). Pins:
- the `coding_agent` node-kind compiles + runs through the generic engine;
- the seam receives the task / workspace / model / bounds from the WorkflowDef params;
- the CodingResult (incl. a bounded `stopped_limit`) is threaded back unchanged.
"""

import pytest

import coding_orchestrator as CO
import engine
from coding_agent import CodingResult
from workflows import CODING_DEF, Branch, Node, WorkflowDef


class _FakeSeam:
    """Deterministic stand-in for run_coding_agent: records the call, returns a fixed
    CodingResult (optionally writing a real file so the diff is genuine)."""

    def __init__(self, result: CodingResult):
        self.result = result
        self.calls: list[dict] = []

    def __call__(self, task, workspace, *, model, max_turns, max_tool_calls,
                 max_budget_usd, feedback=None, **kw):
        self.calls.append({
            "task": task, "workspace": workspace, "model": model,
            "max_turns": max_turns, "max_tool_calls": max_tool_calls,
            "max_budget_usd": max_budget_usd, "feedback": feedback,
        })
        return self.result


def _result(status="completed", summary="did it", diff="--- a\n+++ b\n", files=("f.py",)):
    return CodingResult(summary=summary, diff=diff, changed_files=list(files), status=status)


def test_build_coding_runs_the_seam_and_returns_result():
    seam = _FakeSeam(_result())
    out = CO.build_coding("add a function", "/tmp/ws", model="m", coding_fn=seam)
    assert out == _result()
    assert len(seam.calls) == 1
    call = seam.calls[0]
    assert call["task"] == "add a function" and call["workspace"] == "/tmp/ws"
    assert call["model"] == "m"


def test_build_coding_threads_bounds_from_params():
    seam = _FakeSeam(_result())
    CO.build_coding(
        "t", "/tmp/ws", model="m",
        max_turns=3, max_tool_calls=7, max_budget_usd=0.5, coding_fn=seam,
    )
    call = seam.calls[0]
    assert (call["max_turns"], call["max_tool_calls"], call["max_budget_usd"]) == (3, 7, 0.5)


def test_build_coding_defaults_match_coding_def_params():
    seam = _FakeSeam(_result())
    CO.build_coding("t", "/tmp/ws", model="m", coding_fn=seam)
    call = seam.calls[0]
    assert call["max_turns"] == CODING_DEF.params["max_turns"]
    assert call["max_tool_calls"] == CODING_DEF.params["max_tool_calls"]
    assert call["max_budget_usd"] == CODING_DEF.params["max_budget_usd"]


def test_build_coding_passes_through_stopped_limit():
    seam = _FakeSeam(_result(status="stopped_limit", diff="(partial)", files=("x.py",)))
    out = CO.build_coding("t", "/tmp/ws", model="m", coding_fn=seam)
    assert out.status == "stopped_limit" and out.changed_files == ["x.py"]


def test_coding_agent_node_kind_compiles_through_the_generic_engine():
    # the new node-kind is wired into the engine like a step (handler + edge).
    seam = _FakeSeam(_result())
    wf = WorkflowDef(
        id="coding", entry="coding", output_ref="coding",
        nodes=(Node("coding", "coding_agent", handler_ref="coding_run",
                    config_key="coding_fn", next="__end__"),),
    )
    out = CO.build_coding("t", "/tmp/ws", model="m", coding_fn=seam, wf=wf)
    assert out.status == "completed"


def test_engine_rejects_coding_agent_node_with_unregistered_handler():
    from typing import TypedDict

    class _S(TypedDict, total=False):
        result: dict

    wf = WorkflowDef(
        id="x", entry="c", output_ref="coding",
        nodes=(Node("c", "coding_agent", handler_ref="missing", next="__end__"),),
    )
    with pytest.raises(ValueError, match="unregistered handler 'missing'"):
        engine.build_graph(wf, _S, node_handlers={}, predicates={})
