"""Offline contract tests for the agents (no network, no SDK).

Two layers:
- the parsers (`parse_digest`, `parse_critique`) are pure — tested directly;
- `summarize_agent` / `verify_agent` run with an injected fake `llm`, so they
  exercise prompt→parse→validate fully offline (see the agent-function tests).
"""

import json

import pytest

from agent import (
    AgentContractError,
    Critique,
    Digest,
    parse_critique,
    parse_digest,
)
from fetch import FeedItem

SRC = [
    FeedItem("Title A", "https://e/a", "body a", "p"),
    FeedItem("Title B", "https://e/b", "body b", "p"),
]


def _digest_reply(objs):
    return json.dumps(objs)


# --- parse_digest ----------------------------------------------------------


def test_parse_digest_valid():
    raw = _digest_reply(
        [
            {"title": "Title A", "link": "https://e/a", "one_line_summary": "sum a"},
            {"title": "Title B", "link": "https://e/b", "one_line_summary": "sum b"},
        ]
    )
    digest = parse_digest(raw, SRC)
    assert isinstance(digest, Digest)
    assert [(i.title, i.link, i.one_line_summary) for i in digest.items] == [
        ("Title A", "https://e/a", "sum a"),
        ("Title B", "https://e/b", "sum b"),
    ]


def test_parse_digest_repairs_title_and_link_from_source():
    # Model fabricates a link and rewrites a title; parser ignores both and uses
    # the source values verbatim (by position). Only the summary is the model's.
    raw = _digest_reply(
        [
            {"title": "WRONG", "link": "https://evil/x", "one_line_summary": "sum a"},
            {"title": "also wrong", "link": "", "one_line_summary": "sum b"},
        ]
    )
    digest = parse_digest(raw, SRC)
    assert [(i.title, i.link) for i in digest.items] == [
        ("Title A", "https://e/a"),
        ("Title B", "https://e/b"),
    ]
    assert [i.one_line_summary for i in digest.items] == ["sum a", "sum b"]


def test_parse_digest_tolerates_fences_and_prose():
    raw = "Here:\n```json\n" + _digest_reply(
        [
            {"one_line_summary": "sum a"},
            {"one_line_summary": "sum b"},
        ]
    ) + "\n```"
    digest = parse_digest(raw, SRC)
    assert len(digest.items) == 2
    # title/link still come from source even when the model omits them entirely.
    assert digest.items[0].title == "Title A"


def test_parse_digest_wrong_count_raises():
    raw = _digest_reply([{"one_line_summary": "only one"}])
    with pytest.raises(AgentContractError, match="expected 2"):
        parse_digest(raw, SRC)


@pytest.mark.parametrize("bad_summary", ["", "   ", None, 123])
def test_parse_digest_bad_summary_raises(bad_summary):
    raw = _digest_reply(
        [
            {"one_line_summary": "ok"},
            {"one_line_summary": bad_summary},
        ]
    )
    with pytest.raises(AgentContractError, match="one_line_summary"):
        parse_digest(raw, SRC)


def test_parse_digest_not_an_array_raises():
    with pytest.raises(AgentContractError, match="not a JSON array"):
        parse_digest('{"one_line_summary": "x"}', SRC)


# --- parse_critique --------------------------------------------------------


def test_parse_critique_passed_true():
    c = parse_critique('{"passed": true, "issues": []}')
    assert c == Critique(passed=True, issues=[])


def test_parse_critique_passed_true_clears_stray_issues():
    # A passing critique that still lists issues → pass is pass, issues dropped.
    raw = '{"passed": true, "issues": [{"kind": "hallucination", "detail": "x"}]}'
    c = parse_critique(raw)
    assert c.passed is True
    assert c.issues == []


def test_parse_critique_failed_with_issue():
    raw = json.dumps(
        {
            "passed": False,
            "issues": [
                {"index": 2, "kind": "summary_inaccurate", "detail": "distorts source"}
            ],
        }
    )
    c = parse_critique(raw)
    assert c.passed is False
    assert len(c.issues) == 1
    assert c.issues[0].index == 2
    assert c.issues[0].kind == "summary_inaccurate"
    assert c.issues[0].detail == "distorts source"


def test_parse_critique_failed_without_issues_raises():
    with pytest.raises(AgentContractError, match="at least one issue"):
        parse_critique('{"passed": false, "issues": []}')


def test_parse_critique_failed_missing_issues_raises():
    with pytest.raises(AgentContractError, match="at least one issue"):
        parse_critique('{"passed": false}')


@pytest.mark.parametrize("bad", ['{"passed": "yes", "issues": []}', '{"issues": []}'])
def test_parse_critique_non_bool_passed_raises(bad):
    with pytest.raises(AgentContractError, match="passed"):
        parse_critique(bad)


def test_parse_critique_not_an_object_raises():
    with pytest.raises(AgentContractError, match="not a JSON object"):
        parse_critique("[1, 2, 3]")


def test_parse_critique_issue_without_detail_raises():
    raw = '{"passed": false, "issues": [{"kind": "format"}]}'
    with pytest.raises(AgentContractError, match="detail"):
        parse_critique(raw)


def test_parse_critique_lenient_kind_and_index():
    # Unknown kind kept; bool index (true) is NOT read as index 1; missing → None.
    raw = json.dumps(
        {
            "passed": False,
            "issues": [
                {"index": True, "kind": "weird-kind", "detail": "d1"},
                {"detail": "d2"},
            ],
        }
    )
    c = parse_critique(raw)
    assert c.issues[0].index is None  # bool excluded
    assert c.issues[0].kind == "weird-kind"
    assert c.issues[1].index is None  # missing
    assert c.issues[1].kind == "unspecified"
