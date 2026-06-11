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
from agent import AgentContractError
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


# --- Phase 10c: the automatic coder↔reviewer dialogue -----------------------

class _ScriptedSeam:
    """Returns a CodingResult per call (so a redo gets a fresh result), recording
    each call's feedback so we can assert the reviewer's feedback reached the coder."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, task, workspace, *, model, max_turns, max_tool_calls,
                 max_budget_usd, feedback=None, **kw):
        self.calls.append({"feedback": feedback})
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


class _ScriptedReviewer:
    def __init__(self, *verdicts):
        self.verdicts = list(verdicts)
        self.calls = []

    def __call__(self, task, result, *, model):
        self.calls.append({"task": task, "status": result.get("status")})
        out = self.verdicts.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _approve():
    return {"approved": True, "summary": "lgtm", "issues": []}


def _reject(detail="fix the bug"):
    return {"approved": False, "summary": "no", "issues": [{"severity": "major", "detail": detail}]}


def test_auto_review_off_skips_the_reviewer():
    # The default (pre-10c) path: no reviewer call, no review verdict.
    seam = _ScriptedSeam(_result())
    reviewer = _ScriptedReviewer(_approve())
    out = CO.build_coding("t", "/tmp/ws", model="m", coding_fn=seam, reviewer_fn=reviewer)
    assert reviewer.calls == []
    assert out.review_verdict is None and out.review_rounds == 0


def test_auto_review_approves_first_round():
    seam = _ScriptedSeam(_result())
    reviewer = _ScriptedReviewer(_approve())
    out = CO.build_coding(
        "t", "/tmp/ws", model="m", coding_fn=seam, reviewer_fn=reviewer, auto_review=True
    )
    assert len(seam.calls) == 1 and len(reviewer.calls) == 1
    assert out.review_verdict == "approved" and out.review_rounds == 1


def test_auto_review_rejects_then_approves_and_feeds_back():
    seam = _ScriptedSeam(_result(summary="v1"), _result(summary="v2"))
    reviewer = _ScriptedReviewer(_reject("null deref on line 3"), _approve())
    out = CO.build_coding(
        "t", "/tmp/ws", model="m", coding_fn=seam, reviewer_fn=reviewer, auto_review=True
    )
    # coder ran twice (initial + 1 redo); reviewer twice; converged approved.
    assert len(seam.calls) == 2 and len(reviewer.calls) == 2
    assert out.review_verdict == "approved" and out.review_rounds == 2
    # the reviewer's issue reached the coder as feedback on the redo.
    assert "null deref on line 3" in (seam.calls[1]["feedback"] or "")


def test_auto_review_not_converged_at_cap():
    seam = _ScriptedSeam(_result(summary="v1"), _result(summary="v2"))
    reviewer = _ScriptedReviewer(_reject(), _reject())  # never approves
    out = CO.build_coding(
        "t", "/tmp/ws", model="m", coding_fn=seam, reviewer_fn=reviewer,
        auto_review=True, max_review_rounds=2,
    )
    assert len(reviewer.calls) == 2  # bounded
    assert out.review_verdict == "not_converged" and out.review_rounds == 2
    assert len(out.review_issues) == 1  # last round's issues surfaced


def test_auto_review_contract_violation_degrades_to_approve():
    seam = _ScriptedSeam(_result())
    reviewer = _ScriptedReviewer(AgentContractError("garbage"))
    out = CO.build_coding(
        "t", "/tmp/ws", model="m", coding_fn=seam, reviewer_fn=reviewer, auto_review=True
    )
    # a misbehaving reviewer never traps the run — treated as approve.
    assert out.review_verdict == "approved" and out.review_rounds == 1


def test_auto_review_skips_a_failed_coder_result():
    # a hard `failed` seam result has nothing to review → reviewer not called.
    seam = _ScriptedSeam(_result(status="failed"))
    reviewer = _ScriptedReviewer(_approve())
    out = CO.build_coding(
        "t", "/tmp/ws", model="m", coding_fn=seam, reviewer_fn=reviewer, auto_review=True
    )
    assert reviewer.calls == [] and out.review_verdict is None


def test_auto_review_skips_a_git_tampered_result():
    # A `.git`-tampered result has nothing safe to review → reviewer not called; the
    # family still refuses to finalize it (the 10b-2 guard, tested in the runner).
    seam = _ScriptedSeam(CodingResult(
        summary="s", diff="d", changed_files=["f"], status="completed",
        git_tampered=["hooks/pre-commit"],
    ))
    reviewer = _ScriptedReviewer(_approve())
    out = CO.build_coding(
        "t", "/tmp/ws", model="m", coding_fn=seam, reviewer_fn=reviewer, auto_review=True
    )
    assert reviewer.calls == [] and out.review_verdict is None
    assert out.git_tampered == ["hooks/pre-commit"]  # preserved for the finalize guard
