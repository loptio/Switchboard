"""Offline tests for the human-in-the-loop suspend/resume primitive (Unit 3).

Uses a real checkpointer (InMemorySaver) and injected fake agents — no network,
no SDK, no Postgres. The "simulated restart" is genuine: resume_review_run
compiles a FRESH app from the shared builder bound to the SAME saver, so a
successful resume proves the state came from the checkpoint, not in-process
memory. Mirrors the validated spike.
"""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import orchestrator
from agent import AgentContractError, Critique, CritiqueIssue, Digest, DigestItem
from fetch import FeedItem

ITEMS = [
    FeedItem("A", "https://e/a", "ba", "p"),
    FeedItem("B", "https://e/b", "bb", "p"),
]
PASS = Critique(passed=True, issues=[])
THREAD = "run-abc"


def _digest(tag):
    return Digest([DigestItem("A", "https://e/a", tag)])


def _json_native(obj):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return True
    if isinstance(obj, list):
        return all(_json_native(x) for x in obj)
    if isinstance(obj, dict):
        return all(isinstance(k, str) and _json_native(v) for k, v in obj.items())
    return False


class FakeSummarizer:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, items, n, model, *, feedback=None):
        self.calls.append({"feedback": feedback})
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


class FakeVerifier:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, digest, items, model):
        self.calls.append(digest)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def test_review_run_suspends_at_gate_with_clean_payload():
    saver = InMemorySaver()
    s, v = FakeSummarizer(_digest("d1")), FakeVerifier(PASS)
    out = orchestrator.start_review_run(
        ITEMS, 2, "m", thread_id=THREAD, checkpointer=saver, summarize_fn=s, verify_fn=v
    )
    assert out.status == "suspended"
    # the review contract: the candidate digest (JSON) + open issues (none on a pass)
    assert out.payload["digest"] == orchestrator._digest_to_dict(_digest("d1"))
    assert out.payload["issues"] == []
    assert len(s.calls) == 1 and len(v.calls) == 1  # auto-loop ran once, then paused


def test_resume_approve_completes_after_simulated_restart():
    saver = InMemorySaver()
    orchestrator.start_review_run(
        ITEMS, 2, "m", thread_id=THREAD, checkpointer=saver,
        summarize_fn=FakeSummarizer(_digest("d1")), verify_fn=FakeVerifier(PASS),
    )
    # Fresh fake agents standing in for a brand-new resume process; approve must
    # NOT re-run any agent (it just accepts the persisted digest).
    s2, v2 = FakeSummarizer(), FakeVerifier()
    out = orchestrator.resume_review_run(
        thread_id=THREAD, checkpointer=saver, decision={"action": "approve"},
        summarize_fn=s2, verify_fn=v2,
    )
    assert out.status == "completed"
    assert out.digest == _digest("d1")  # the digest restored from the checkpoint
    assert s2.calls == [] and v2.calls == []


def test_resume_redo_reruns_with_human_feedback_then_re_presents():
    saver = InMemorySaver()
    s = FakeSummarizer(_digest("d1"), _digest("d2"))
    v = FakeVerifier(PASS, PASS)
    orchestrator.start_review_run(
        ITEMS, 2, "m", thread_id=THREAD, checkpointer=saver, summarize_fn=s, verify_fn=v
    )
    # human asks for a redo with feedback → fresh auto-loop produces d2 → re-present
    out = orchestrator.resume_review_run(
        thread_id=THREAD, checkpointer=saver,
        decision={"action": "redo", "feedback": "more detail please"},
        summarize_fn=s, verify_fn=v,
    )
    assert out.status == "suspended"
    assert out.payload["digest"] == orchestrator._digest_to_dict(_digest("d2"))
    # the human's feedback reached the re-summarize as a "human" critique issue
    fb = s.calls[-1]["feedback"]
    assert fb is not None and fb.issues[0].kind == "human"
    assert fb.issues[0].detail == "more detail please"
    # finally approve → completed with the redone digest
    out2 = orchestrator.resume_review_run(
        thread_id=THREAD, checkpointer=saver, decision={"action": "approve"},
        summarize_fn=s, verify_fn=v,
    )
    assert out2.status == "completed" and out2.digest == _digest("d2")


def test_checkpointed_state_is_json_native_no_dataclass():
    # H3: the persisted state must never contain a dataclass (that would hit
    # LangGraph's deprecated, will-be-blocked serializer path).
    saver = InMemorySaver()
    orchestrator.start_review_run(
        ITEMS, 2, "m", thread_id=THREAD, checkpointer=saver,
        summarize_fn=FakeSummarizer(_digest("d1")), verify_fn=FakeVerifier(PASS),
    )
    app = orchestrator._BUILDER.compile(checkpointer=saver)
    snap = app.get_state({"configurable": {"thread_id": THREAD}})
    for key, val in snap.values.items():
        assert _json_native(val), f"channel {key!r} is not JSON-native: {val!r}"


def test_review_run_give_up_raises():
    saver = InMemorySaver()
    s = FakeSummarizer(*[AgentContractError("x") for _ in range(3)])
    with pytest.raises(RuntimeError, match="never produced"):
        orchestrator.start_review_run(
            ITEMS, 2, "m", thread_id=THREAD, checkpointer=saver, max_redos=2,
            summarize_fn=s, verify_fn=FakeVerifier(),
        )


def test_digest_default_path_does_not_suspend():
    # review off (build_digest, no checkpointer) runs straight through — regression
    # guard that the human-review gate is truly opt-in.
    out = orchestrator.build_digest(
        ITEMS, 2, "m", summarize_fn=FakeSummarizer(_digest("d1")), verify_fn=FakeVerifier(PASS)
    )
    assert out == _digest("d1")
