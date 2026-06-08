"""Offline tests for the brief orchestrator's control flow (no network, no SDK).

The three agents are injected as fakes (scripted per call), so these exercise the
deterministic graph only: filter → per-item (summary + N perspectives) → assemble.
Call counts are asserted to prove the fan-out shape and the cost cap.
"""

from datetime import date

import pytest

from agent import AgentContractError
from brief_agent import Brief, BriefItem, Perspective
from brief_orchestrator import build_brief
from sources import SourceItem

DAY = date(2026, 6, 8)


def _src(n):
    return [
        SourceItem(f"T{i}", f"https://e/{i}", "Src", "科技", "pub", f"text{i}")
        for i in range(1, n + 1)
    ]


class FakeFilter:
    """Each outcome: a list[SourceItem] to keep, or an Exception to raise."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, items, model, *, keep_cap):
        self.calls.append({"n": len(items), "keep_cap": keep_cap})
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


class FakeSummarize:
    """Scripted outcomes (str / Exception); defaults to 'S:<title>' when exhausted."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, item, model):
        self.calls.append(item.title)
        if self.outcomes:
            out = self.outcomes.pop(0)
            if isinstance(out, Exception):
                raise out
            return out
        return f"S:{item.title}"


class FakePerspective:
    """Scripted outcomes (Perspective / str / Exception); defaults per stance."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, item, stance, model):
        self.calls.append((item.title, stance))
        if self.outcomes:
            out = self.outcomes.pop(0)
            if isinstance(out, Exception):
                raise out
            return out if isinstance(out, Perspective) else Perspective(stance, out)
        return Perspective(stance, f"take:{stance}:{item.title}")


def _build(items, f, s, p, **kw):
    return build_brief(
        items, model="m", day=DAY, filter_fn=f, summarize_fn=s, perspective_fn=p, **kw
    )


def test_filter_then_summary_and_three_perspectives():
    items = _src(4)
    f = FakeFilter([items[0], items[2]])  # keep T1, T3
    s, p = FakeSummarize(), FakePerspective()

    brief = _build(items, f, s, p)

    assert isinstance(brief, Brief) and brief.date == "2026-06-08"
    assert [it.title for it in brief.items] == ["T1", "T3"]
    # one filter call; one summary per kept; one perspective per (kept × stance)
    assert len(f.calls) == 1
    assert s.calls == ["T1", "T3"]
    assert p.calls == [
        ("T1", "商业"), ("T1", "政策"), ("T1", "技术"),
        ("T3", "商业"), ("T3", "政策"), ("T3", "技术"),
    ]
    first = brief.items[0]
    assert first.summary == "S:T1"
    assert [persp.stance for persp in first.perspectives] == ["商业", "政策", "技术"]
    assert first.perspectives[0].take == "take:商业:T1"


def test_provenance_comes_from_source_not_agents():
    items = _src(1)
    f = FakeFilter([items[0]])
    # agents only produce summary/take; title/link/source/domain must stay the source's
    s = FakeSummarize("a totally different summary")
    p = FakePerspective()

    item = _build(items, f, s, p).items[0]

    assert (item.title, item.link, item.source, item.domain) == (
        "T1", "https://e/1", "Src", "科技",
    )


def test_keep_cap_enforced_even_if_filter_over_returns():
    items = _src(10)
    f = FakeFilter(items)  # filter (mis)returns all 10
    s, p = FakeSummarize(), FakePerspective()

    brief = _build(items, f, s, p, keep_cap=3)

    assert len(brief.items) == 3  # orchestrator truncates to keep_cap
    assert len(s.calls) == 3 and len(p.calls) == 9


def test_custom_stances():
    items = _src(1)
    f = FakeFilter([items[0]])
    s, p = FakeSummarize(), FakePerspective()

    brief = _build(items, f, s, p, stances=("法律", "伦理"))

    assert [persp.stance for persp in brief.items[0].perspectives] == ["法律", "伦理"]
    assert p.calls == [("T1", "法律"), ("T1", "伦理")]


def test_empty_input_short_circuits_without_calling_agents():
    f, s, p = FakeFilter(), FakeSummarize(), FakePerspective()

    brief = build_brief([], model="m", day=DAY, filter_fn=f, summarize_fn=s, perspective_fn=p)

    assert brief == Brief(date="2026-06-08", items=[])
    assert f.calls == [] and s.calls == [] and p.calls == []


def test_filter_keeps_nothing_yields_empty_brief():
    items = _src(3)
    f = FakeFilter([])  # everything was noise
    s, p = FakeSummarize(), FakePerspective()

    brief = _build(items, f, s, p)

    assert brief.items == []
    assert len(f.calls) == 1 and s.calls == [] and p.calls == []


def test_bounded_retry_recovers_from_one_malformed_summary():
    items = _src(1)
    f = FakeFilter([items[0]])
    s = FakeSummarize(AgentContractError("bad"), "S:recovered")  # fail once, then ok
    p = FakePerspective()

    item = _build(items, f, s, p).items[0]

    assert item.summary == "S:recovered"
    assert len(s.calls) == 2  # retried once


def test_persistently_malformed_filter_fails_the_run():
    items = _src(2)
    f = FakeFilter(AgentContractError("x"), AgentContractError("x"))  # always bad
    s, p = FakeSummarize(), FakePerspective()

    with pytest.raises(RuntimeError, match="filter"):
        _build(items, f, s, p)
    assert len(f.calls) == 2  # bounded: MAX_ATTEMPTS tries, then give up
