"""Meta orchestrator (Phase 9) — graph behaviour with a scripted drafting seam.

Offline: the draft_fn is a scripted fake (no SDK / no key); the checkpointer is an
InMemorySaver. Covers the three validate routes (pass → gate, errors → bounded
redo, exhausted → give_up), the approve/redo gate semantics, and the contract-error
path (a malformed reply counts as a failed attempt, it never crashes the run)."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import workflows
from agent import AgentContractError
from meta_orchestrator import (
    DEFAULT_MAX_REDOS,
    resume_meta_review_run,
    start_meta_review_run,
)


def _digest_clone(wf_id="meta-made"):
    wf = workflows.workflow_def_to_dict(workflows.DIGEST_DEF)
    wf["id"] = wf_id
    return wf


def _valid(wf_id="meta-made"):
    return {"workflow_def": _digest_clone(wf_id), "agent_defs": [], "explanation": "ok"}


def _invalid():
    return {"workflow_def": _digest_clone("news"), "agent_defs": [], "explanation": "collides"}


class _D:
    """Scripted drafting seam: pops one proposal (or raises one exception) per call,
    recording the redo context each call received."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def __call__(self, request, *, model, prior=None, errors=None, feedback=None):
        self.calls.append({"prior": prior, "errors": errors, "feedback": feedback})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _start(draft_fn, saver=None, thread_id="t1", **kw):
    return start_meta_review_run(
        "做一个数字摘要变体",
        model="m",
        thread_id=thread_id,
        checkpointer=saver or InMemorySaver(),
        draft_fn=draft_fn,
        **kw,
    )


def test_valid_first_draft_suspends_at_the_gate():
    d = _D(_valid())
    outcome = _start(d)
    assert outcome.status == "suspended"
    p = outcome.payload["proposal"]
    assert p["workflow_def"]["id"] == "meta-made"
    assert p["attempts"] == 1
    assert p["request"] == "做一个数字摘要变体"
    assert d.calls[0] == {"prior": None, "errors": None, "feedback": None}


def test_validator_errors_feed_the_redo():
    d = _D(_invalid(), _valid())
    outcome = _start(d)
    assert outcome.status == "suspended"
    assert len(d.calls) == 2
    # the second draft received the first attempt's validator errors + its proposal
    assert any("already exists" in e for e in d.calls[1]["errors"])
    assert d.calls[1]["prior"]["workflow_def"]["id"] == "news"


def test_contract_error_counts_as_a_failed_attempt():
    d = _D(AgentContractError("not json"), _valid())
    outcome = _start(d)
    assert outcome.status == "suspended"
    assert len(d.calls) == 2
    assert any("violated the proposal contract" in e for e in d.calls[1]["errors"])
    assert d.calls[1]["prior"] is None  # the failed attempt produced no proposal


def test_exhausted_redos_give_up_completed_unapproved():
    d = _D(_invalid(), _invalid(), _invalid())
    outcome = _start(d)  # max_redos=2 → 3 total drafts, then give_up
    assert outcome.status == "completed"
    assert outcome.result["approved"] is False
    assert outcome.result["attempts"] == DEFAULT_MAX_REDOS + 1
    assert any("already exists" in e for e in outcome.result["errors"])
    assert len(d.calls) == DEFAULT_MAX_REDOS + 1


def test_approve_completes_with_the_proposal():
    saver = InMemorySaver()
    d = _D(_valid())
    assert _start(d, saver).status == "suspended"
    outcome = resume_meta_review_run(
        thread_id="t1", checkpointer=saver, decision={"action": "approve"}, draft_fn=d
    )
    assert outcome.status == "completed"
    assert outcome.result["approved"] is True
    assert outcome.result["proposal"]["workflow_def"]["id"] == "meta-made"


def test_redo_threads_feedback_and_resets_the_budget():
    saver = InMemorySaver()
    d = _D(_valid("first-take"), _valid("second-take"))
    assert _start(d, saver).status == "suspended"
    outcome = resume_meta_review_run(
        thread_id="t1",
        checkpointer=saver,
        decision={"action": "redo", "feedback": "换个名字"},
        draft_fn=d,
    )
    assert outcome.status == "suspended"
    assert outcome.payload["proposal"]["workflow_def"]["id"] == "second-take"
    # the redo draft received the human feedback and the prior proposal,
    # and the gate reset the attempt budget (a fresh 1, not 2).
    assert d.calls[1]["feedback"] == "换个名字"
    assert d.calls[1]["prior"]["workflow_def"]["id"] == "first-take"
    assert outcome.payload["proposal"]["attempts"] == 1


def test_max_redos_param_overrides_the_default():
    d = _D(_invalid(), _invalid())
    outcome = _start(d, max_redos=1)
    assert outcome.status == "completed" and outcome.result["approved"] is False
    assert len(d.calls) == 2


def test_meta_def_compiles_via_the_module_graph():
    # The prebuilt module builder exists and META_DEF is registered for dispatch.
    assert workflows.WORKFLOWS["meta"].output_ref == "meta"
    with pytest.raises(KeyError):
        workflows.WORKFLOWS["nope"]
