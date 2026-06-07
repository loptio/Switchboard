"""Offline tests for the orchestrator's control flow (no network, no SDK).

The agents are injected as fakes (scripted per attempt), so these exercise the
deterministic loop only: pass, fail→redo→pass, cap→accept-last, malformed
verifier→accept, dirty summarizer→redo / hard-fail, empty input. Call counts are
asserted to prove the loop is bounded.
"""

import logging

import pytest

import orchestrator
from agent import AgentContractError, Critique, CritiqueIssue, Digest, DigestItem
from fetch import FeedItem
from orchestrator import build_digest

ITEMS = [
    FeedItem("A", "https://e/a", "ba", "p"),
    FeedItem("B", "https://e/b", "bb", "p"),
]

PASS = Critique(passed=True, issues=[])


def _digest(tag):
    return Digest([DigestItem("A", "https://e/a", tag)])


def _fail(detail="bad"):
    return Critique(
        passed=False, issues=[CritiqueIssue(1, "summary_inaccurate", detail)]
    )


class FakeSummarizer:
    """Each outcome is a Digest to return or an Exception to raise, in order."""

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


def test_pass_on_first_attempt():
    s = FakeSummarizer(_digest("d1"))
    v = FakeVerifier(PASS)
    out = build_digest(ITEMS, 2, "m", summarize_fn=s, verify_fn=v)
    assert out == _digest("d1")
    assert len(s.calls) == 1 and len(v.calls) == 1
    assert s.calls[0]["feedback"] is None


def test_fail_then_redo_then_pass():
    s = FakeSummarizer(_digest("d1"), _digest("d2"))
    fail = _fail("too vague")
    v = FakeVerifier(fail, PASS)
    out = build_digest(ITEMS, 2, "m", summarize_fn=s, verify_fn=v)
    assert out == _digest("d2")
    assert len(s.calls) == 2 and len(v.calls) == 2
    # the critique was fed back into the second summarize call.
    assert s.calls[1]["feedback"] == fail


def test_cap_reached_accepts_last_version(caplog):
    s = FakeSummarizer(_digest("d1"), _digest("d2"), _digest("d3"))
    v = FakeVerifier(_fail(), _fail(), _fail())
    with caplog.at_level(logging.WARNING):
        out = build_digest(ITEMS, 2, "m", max_redos=2, summarize_fn=s, verify_fn=v)
    assert out == _digest("d3")  # last version accepted
    assert len(s.calls) == 3 and len(v.calls) == 3  # bounded: 1 initial + 2 redos
    assert any("redo limit (2) reached" in r.message for r in caplog.records)


def test_verifier_malformed_accepts_current(caplog):
    s = FakeSummarizer(_digest("d1"))
    v = FakeVerifier(AgentContractError("bad1"), AgentContractError("bad2"))
    with caplog.at_level(logging.WARNING):
        out = build_digest(ITEMS, 2, "m", summarize_fn=s, verify_fn=v)
    assert out == _digest("d1")
    assert len(s.calls) == 1 and len(v.calls) == 2  # re-verified once, then accept
    assert any("inconclusive" in r.message for r in caplog.records)


def test_summarizer_dirty_then_valid_redoes_with_format_feedback():
    s = FakeSummarizer(AgentContractError("dirty"), _digest("d2"))
    v = FakeVerifier(PASS)
    out = build_digest(ITEMS, 2, "m", summarize_fn=s, verify_fn=v)
    assert out == _digest("d2")
    assert len(s.calls) == 2 and len(v.calls) == 1
    # the redo received the fixed, instructional format feedback.
    assert s.calls[1]["feedback"] is not None
    assert s.calls[1]["feedback"].issues[0].kind == "format"


def test_summarizer_always_dirty_raises():
    s = FakeSummarizer(
        AgentContractError("d0"), AgentContractError("d1"), AgentContractError("d2")
    )
    v = FakeVerifier()  # never reached
    with pytest.raises(RuntimeError, match="never produced"):
        build_digest(ITEMS, 2, "m", max_redos=2, summarize_fn=s, verify_fn=v)
    assert len(s.calls) == 3 and len(v.calls) == 0


def test_dirty_on_last_attempt_falls_back_to_prior_valid_digest(caplog):
    # attempt 1 valid (verify fails), attempt 2 valid (verify fails), attempt 3
    # dirty → no budget left but we have a prior valid digest → accept it.
    s = FakeSummarizer(_digest("d1"), _digest("d2"), AgentContractError("dirty"))
    v = FakeVerifier(_fail(), _fail())
    with caplog.at_level(logging.WARNING):
        out = build_digest(ITEMS, 2, "m", max_redos=2, summarize_fn=s, verify_fn=v)
    assert out == _digest("d2")  # last schema-valid digest
    assert len(s.calls) == 3 and len(v.calls) == 2


def test_empty_items_short_circuits():
    s = FakeSummarizer()
    v = FakeVerifier()
    out = build_digest([], 2, "m", summarize_fn=s, verify_fn=v)
    assert out == Digest(items=[])
    assert s.calls == [] and v.calls == []


def test_engine_is_a_compiled_langgraph_with_expected_nodes():
    # The control flow is genuinely a compiled LangGraph StateGraph (Unit 2), not
    # a hand-rolled loop pretending to be one.
    nodes = set(orchestrator._APP.get_graph().nodes)
    assert {"summarize", "verify", "accept_last"} <= nodes


def test_inconclusive_after_a_real_failure_degrades_not_fakes_pass(caplog):
    # "Never fake a pass" on its dangerous path: a REAL failing critique, redo,
    # then the verifier goes malformed → accept the CURRENT (post-redo) digest as
    # inconclusive — not the original, and not a faked pass. (Distinct from the
    # first-attempt-inconclusive case where original == current.)
    s = FakeSummarizer(_digest("d1"), _digest("d2"))
    v = FakeVerifier(_fail(), AgentContractError("bad"), AgentContractError("bad"))
    with caplog.at_level(logging.INFO):
        out = build_digest(ITEMS, 2, "m", summarize_fn=s, verify_fn=v)
    assert out == _digest("d2")  # the current post-redo digest, NOT d1
    assert len(s.calls) == 2 and len(v.calls) == 3  # 1 fail + 2 re-verify attempts
    msgs = [r.message for r in caplog.records]
    assert any("inconclusive" in m for m in msgs)
    assert not any("redo limit" in m for m in msgs)  # not a cap-reached accept
    assert not any("accepted on attempt" in m for m in msgs)  # not a faked pass


def test_max_redos_zero_takes_first_digest_no_redo(caplog):
    # Boundary: max_redos=0 means exactly one attempt, no redo even on failure.
    s = FakeSummarizer(_digest("d1"))
    v = FakeVerifier(_fail())
    with caplog.at_level(logging.WARNING):
        out = build_digest(ITEMS, 2, "m", max_redos=0, summarize_fn=s, verify_fn=v)
    assert out == _digest("d1")
    assert len(s.calls) == 1 and len(v.calls) == 1  # no redo
    assert s.calls[0]["feedback"] is None  # never asked to redo
    assert any("redo limit (0) reached" in r.message for r in caplog.records)


def test_large_max_redos_stays_bounded_and_within_recursion_limit():
    # max_redos=5, all failing → 6 summarize + 6 verify, accept last. Exercises the
    # recursion_limit formula (2*(max_redos+1)+10) — no RecursionError.
    s = FakeSummarizer(*[_digest(f"d{i}") for i in range(1, 7)])
    v = FakeVerifier(*[_fail() for _ in range(6)])
    out = build_digest(ITEMS, 2, "m", max_redos=5, summarize_fn=s, verify_fn=v)
    assert out == _digest("d6")
    assert len(s.calls) == 6 and len(v.calls) == 6
