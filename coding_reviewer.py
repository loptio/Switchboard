"""Coding reviewer (Phase 10c) — an automatic code-review agent, the second voice
in the coder↔reviewer dialogue.

The coding family (Phase 10a/b) was one coder agent + a human gate. 10c inserts an
AUTOMATIC reviewer between them: it reads the coder's diff (+ task, changed files,
commands) and returns a verdict — approve, or specific issues to fix — and the coding
orchestrator loops the coder once per round, bounded, until the reviewer approves or
the round budget runs out. The human still has the final say (the human gate runs
after the dialogue converges).

The reviewer only READS the diff — it needs no tools — so it goes through the
tool-less `llm.py` seam (NOT the coding_agent seam). That keeps the security surface
unchanged: no second sandboxed agent, no network, no new tools. Structurally this
mirrors the digest verifier (`agent.verify_agent`): a strict-JSON judgment over a
candidate, injected as a fake in tests so the whole family stays offline.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from agent import AgentContractError
from agentdefs import render
from config import DEFAULT_LANGUAGE
from llm import complete

_SEVERITIES = ("blocker", "major", "minor")

REVIEWER_SYSTEM_PROMPT = (
    "You are a meticulous senior code reviewer. You receive a TASK and the unified "
    "DIFF a coding agent produced for it (plus the changed files and any shell "
    "commands it ran). Judge ONLY whether the diff correctly and safely accomplishes "
    "the task: correctness, obvious bugs, security, and whether it actually does what "
    "the task asked. Do NOT nitpick style. Approve when the change is correct and "
    "complete; otherwise list specific, actionable issues the coder must fix.\n"
    'Respond with ONLY a JSON object: {"approved": bool, "summary": str, "issues": '
    '[{"severity": str, "detail": str}]}. "severity" is one of '
    + ", ".join(_SEVERITIES)
    + '; "detail" is a concrete, actionable problem. If the diff is correct and '
    'complete, return {"approved": true, "summary": "...", "issues": []}. Otherwise '
    "set approved=false and list each problem. Write summary/detail in {language}. "
    "No prose, no markdown, no code fences."
)


def build_review_prompt(task: str, result: dict) -> str:
    """The reviewer's user message: the task + the coder's diff/files/commands."""
    diff = result.get("diff") or "(empty diff)"
    changed = result.get("changed_files") or []
    commands = result.get("commands") or []
    status = result.get("status", "completed")
    parts = [
        f"TASK:\n{task}",
        f"CODER STATUS: {status}"
        + (
            "  (the coder hit a turn/tool/budget limit — the diff may be partial)"
            if status == "stopped_limit"
            else ""
        ),
        "CHANGED FILES:\n" + ("\n".join(f"- {f}" for f in changed) or "(none)"),
        "COMMANDS THE CODER RAN:\n" + ("\n".join(f"- {c}" for c in commands) or "(none)"),
        "UNIFIED DIFF:\n" + diff,
    ]
    return "\n\n".join(parts)


def parse_review(raw: str) -> dict:
    """Validate a reviewer reply against the contract → a normalized dict
    {approved: bool, summary: str, issues: [{severity, detail}]}. Tolerates fences/
    prose around the JSON object (same posture as the digest/meta parsers). Raises
    AgentContractError on any shape violation — the orchestrator counts it as a failed
    round and, on the last round, degrades gracefully (treats it as unapproved)."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise AgentContractError(f"reviewer reply has no JSON object: {raw[:200]!r}")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AgentContractError(f"reviewer reply is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AgentContractError("reviewer reply is not a JSON object")
    if not isinstance(data.get("approved"), bool):
        raise AgentContractError("reviewer reply: 'approved' must be a bool")

    raw_issues = data.get("issues", [])
    if not isinstance(raw_issues, list):
        raise AgentContractError("reviewer reply: 'issues' must be a list")
    issues: list[dict] = []
    for it in raw_issues:
        if not isinstance(it, dict):
            raise AgentContractError("reviewer reply: each issue must be an object")
        detail = it.get("detail")
        if not isinstance(detail, str) or not detail.strip():
            raise AgentContractError("reviewer reply: each issue needs a non-empty 'detail'")
        sev = it.get("severity")
        severity = sev if sev in _SEVERITIES else "major"
        issues.append({"severity": severity, "detail": detail.strip()})

    summary = data.get("summary", "")
    if not isinstance(summary, str):
        raise AgentContractError("reviewer reply: 'summary' must be a string")
    return {"approved": bool(data["approved"]), "summary": summary, "issues": issues}


def format_feedback(issues: list[dict]) -> str:
    """Render the reviewer's issues as a corrective instruction the coder will see
    appended to the task on the next round (the coding seam already supports
    `feedback`)."""
    if not issues:
        return "The reviewer asked for changes. Re-examine your work and improve it."
    lines = ["A code reviewer found these problems — fix every one:"]
    for it in issues:
        lines.append(f"- [{it.get('severity', 'major')}] {it.get('detail', '')}")
    return "\n".join(lines)


def review_coding(
    task: str,
    result: dict,
    *,
    model: str,
    language: str = DEFAULT_LANGUAGE,
    llm: Callable[..., str] = complete,
    system_prompt: str | None = None,
) -> dict:
    """Reviewer agent: (task, coder result) → normalized verdict dict. The
    `summarize_agent` shape: `llm` is the injectable tool-less seam (tests pass a fake
    `(prompt, *, system_prompt, model) -> str`); strict parse (AgentContractError on
    violation)."""
    prompt = build_review_prompt(task, result)
    sp = render(
        system_prompt if system_prompt is not None else REVIEWER_SYSTEM_PROMPT,
        language=language,
    )
    raw = llm(prompt, system_prompt=sp, model=model)
    return parse_review(raw)
