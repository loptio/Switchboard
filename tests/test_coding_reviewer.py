"""Coding reviewer (Phase 10c) — prompt / parser / agent units, offline.

The reviewer is the tool-less second voice in the coder↔reviewer dialogue. The llm
seam is faked (`llm=` callable), so no SDK / no key / no network.
"""

import json

import pytest

from agent import AgentContractError
from coding_reviewer import (
    REVIEWER_SYSTEM_PROMPT,
    build_review_prompt,
    format_feedback,
    parse_review,
    review_coding,
)

RESULT = {
    "diff": "--- a/x.py\n+++ b/x.py\n+def f(): pass\n",
    "changed_files": ["x.py"],
    "commands": ["python -m pytest -q"],
    "status": "completed",
}


# --- parse_review -----------------------------------------------------------

def test_parse_approved():
    p = parse_review('{"approved": true, "summary": "ok", "issues": []}')
    assert p == {"approved": True, "summary": "ok", "issues": []}


def test_parse_rejected_normalizes_issue_severity():
    raw = json.dumps(
        {"approved": False, "summary": "no", "issues": [{"severity": "weird", "detail": "bug here"}]}
    )
    p = parse_review(raw)
    assert p["approved"] is False
    assert p["issues"] == [{"severity": "major", "detail": "bug here"}]  # unknown sev → major


def test_parse_tolerates_fences():
    p = parse_review('```json\n{"approved": true, "issues": []}\n```')
    assert p["approved"] is True


@pytest.mark.parametrize(
    "reply",
    [
        "no json",
        '{"summary": "x"}',                                  # missing approved
        '{"approved": "yes", "issues": []}',                # approved not a bool
        '{"approved": false, "issues": "x"}',               # issues not a list
        '{"approved": false, "issues": [{"severity": "major"}]}',  # issue missing detail
        '{"approved": false, "issues": [{"detail": ""}]}',  # empty detail
        '{"approved": true,',                                # invalid JSON
    ],
)
def test_parse_rejects_contract_violations(reply):
    with pytest.raises(AgentContractError):
        parse_review(reply)


# --- build_review_prompt + format_feedback ----------------------------------

def test_prompt_carries_task_diff_and_commands():
    prompt = build_review_prompt("add f()", RESULT)
    assert "add f()" in prompt
    assert "def f(): pass" in prompt           # the diff
    assert "python -m pytest -q" in prompt      # the commands
    assert "x.py" in prompt                     # changed files


def test_prompt_flags_a_bounded_stop():
    prompt = build_review_prompt("t", {**RESULT, "status": "stopped_limit"})
    assert "stopped_limit" in prompt and "partial" in prompt


def test_format_feedback_lists_issues():
    fb = format_feedback([{"severity": "blocker", "detail": "null deref"}])
    assert "null deref" in fb and "blocker" in fb


def test_format_feedback_empty_is_generic():
    assert "improve" in format_feedback([]).lower()


# --- review_coding (fake llm) ------------------------------------------------

def test_review_coding_round_trip_with_fake_llm():
    seen = {}

    def fake_llm(prompt, *, system_prompt, model):
        seen.update(prompt=prompt, system_prompt=system_prompt, model=model)
        return '{"approved": false, "summary": "needs work", "issues": [{"severity": "major", "detail": "fix X"}]}'

    out = review_coding("do X", RESULT, model="m", language="简体中文", llm=fake_llm)
    assert out["approved"] is False and out["issues"][0]["detail"] == "fix X"
    assert seen["model"] == "m"
    assert "简体中文" in seen["system_prompt"] and "{language}" not in seen["system_prompt"]
    assert "{language}" in REVIEWER_SYSTEM_PROMPT  # template keeps the marker
