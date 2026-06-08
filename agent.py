"""Agent module — the agents and their output contracts.

The actual model call lives in the `llm.complete` seam (the only module that
imports the SDK); this module builds prompts and validates replies against their
contracts. No tools are granted, so each run is deterministic and non-interactive.

Three agents share that seam:
- `summarize` (Phase 1): items -> Digest, lenient parsing. Still the standalone
  `main.py` path; kept unchanged.
- `summarize_agent` (Phase 5): items (+ optional reviewer feedback) -> Digest,
  validated strictly by `parse_digest` (dirty output never flows downstream).
- `verify_agent` (Phase 5): a candidate Digest + the SOURCE items -> a Critique
  (checks each summary against its source item), validated by `parse_critique`.

The Phase 5 pair is coordinated by `orchestrator.build_digest`; this module holds
no control flow — just prompt-building, the model call, and contract validation.

Auth: the SDK delegates to the Claude Code CLI, which uses your subscription.
Do NOT set ANTHROPIC_API_KEY (that would bill the paid API).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from agentdefs import AGENT_DEFS, ISSUE_KINDS, render
from config import DEFAULT_LANGUAGE
from fetch import FeedItem
from llm import complete

__all__ = [  # names other modules / tests import from here (some re-exported)
    "ISSUE_KINDS",
    "VERIFIER_SYSTEM_PROMPT",
    "AgentContractError",
    "Critique",
    "CritiqueIssue",
    "Digest",
    "DigestItem",
    "parse_critique",
    "parse_digest",
    "summarize",
    "summarize_agent",
    "summary_system_prompt",
    "verify_agent",
]


@dataclass(frozen=True)
class DigestItem:
    title: str
    link: str
    one_line_summary: str


@dataclass(frozen=True)
class Digest:
    items: list[DigestItem]


class AgentContractError(Exception):
    """An agent's reply did not satisfy its output contract.

    Raised by the parsers below so the orchestrator can treat the attempt as a
    failure (redo / re-verify / hard-fail) rather than let dirty data flow
    downstream.
    """


# `ISSUE_KINDS` (the verifier's prompt vocabulary) now lives in `agentdefs` as data
# and is imported above (re-exported here for back-compat). It is NOT enforced as an
# enum — an unknown kind is kept verbatim by `parse_critique`. Note that
# `fabricated_link`/`title_mismatch` are structurally prevented by `parse_digest`
# (title/link come from the source), so in practice the verifier's job is summary
# faithfulness; the kinds remain a vocabulary + unit-tested backstop.


@dataclass(frozen=True)
class CritiqueIssue:
    index: int | None  # 1-based digest item the issue refers to, or None if global
    kind: str          # ideally one of ISSUE_KINDS
    detail: str         # explanation; fed back to the summarizer on redo


@dataclass(frozen=True)
class Critique:
    passed: bool
    issues: list[CritiqueIssue]


def summary_system_prompt(language: str = DEFAULT_LANGUAGE) -> str:
    """Digest summarizer system prompt, parameterized by output `language`.

    The prompt TEXT is data (`agentdefs.AGENT_DEFS["summarize"].system_prompt`);
    this renders the `{language}` marker. The one_line_summary is written in
    `language` even when the source item is in another language; title/link are
    still taken verbatim from the source (anti-fabrication), so provenance is never
    translated."""
    return render(AGENT_DEFS["summarize"].system_prompt, language=language)


# The verifier's system prompt is a constant — sourced as data from agentdefs.
VERIFIER_SYSTEM_PROMPT = AGENT_DEFS["verify"].system_prompt


def _build_prompt(items: list[FeedItem], n: int) -> str:
    chosen = items[:n]
    payload = [
        {"title": it.title, "link": it.link, "summary": it.summary} for it in chosen
    ]
    return (
        f"Summarize these {len(chosen)} items. Return a JSON array of exactly "
        f"{len(chosen)} objects, in the same order.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _parse_json_array(text: str) -> list[dict]:
    """Extract a JSON array of objects from the agent's reply.

    Tolerates stray prose or code fences around the array, and validates the
    shape so a malformed reply fails with a clear message instead of an opaque
    AttributeError later.
    """
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON array in agent output: {text[:200]!r}")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, list) or not all(isinstance(obj, dict) for obj in data):
        raise ValueError(
            f"Agent output is not a JSON array of objects: {text[:200]!r}"
        )
    return data


def _parse_json_object(text: str) -> dict:
    """Extract a single JSON object from a reply, tolerating fences/prose."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object in agent output: {text[:200]!r}")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError(f"Agent output is not a JSON object: {text[:200]!r}")
    return data


def parse_digest(raw: str, chosen: list[FeedItem]) -> Digest:
    """Validate a summarizer reply against the digest contract → a Digest.

    Strict so dirty data never flows downstream. The model's echoed title/link
    are NOT trusted: title and link are taken verbatim from the matched source
    item *by position*, so fabricated/edited links are impossible by construction
    and benign reformatting (HTML entities, Unicode, trimming) can't cause a
    false-reject. The one_line_summary is the model's own work product, so that
    is what we validate (non-empty string). Count must match exactly.

    Raises AgentContractError on any shape/count violation.
    """
    try:
        data = _parse_json_array(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise AgentContractError(f"summarizer reply is not a JSON array: {exc}") from exc

    if len(data) != len(chosen):
        raise AgentContractError(
            f"summarizer returned {len(data)} items; expected {len(chosen)}"
        )

    items: list[DigestItem] = []
    for i, (obj, src) in enumerate(zip(data, chosen), start=1):
        summary = obj.get("one_line_summary")
        if not isinstance(summary, str) or not summary.strip():
            raise AgentContractError(
                f"item {i} has a missing/empty/non-string one_line_summary"
            )
        # title/link verbatim from the source item (by position) — see docstring.
        items.append(
            DigestItem(title=src.title, link=src.link, one_line_summary=summary.strip())
        )
    return Digest(items=items)


def parse_critique(raw: str) -> Critique:
    """Validate a verifier reply against the Critique contract → a Critique.

    Strict: `passed` must be a real bool; a *failing* critique must carry >=1
    well-formed issue (the orchestrator needs actionable feedback to redo); a
    *passing* critique's issues are cleared (pass is pass — avoids a stray issue
    re-triggering a redo). `kind`/`index` are lenient (the verifier may vary) but
    `detail` must be a non-empty string. Raises AgentContractError on any shape
    violation so the orchestrator can re-verify / degrade rather than trust it.
    """
    try:
        data = _parse_json_object(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise AgentContractError(f"verifier reply is not a JSON object: {exc}") from exc

    passed = data.get("passed")
    if not isinstance(passed, bool):
        raise AgentContractError(f"verifier 'passed' is not a boolean: {passed!r}")
    if passed:
        return Critique(passed=True, issues=[])

    raw_issues = data.get("issues")
    if not isinstance(raw_issues, list) or not raw_issues:
        raise AgentContractError("failing critique must list at least one issue")

    issues: list[CritiqueIssue] = []
    for j, item in enumerate(raw_issues, start=1):
        if not isinstance(item, dict):
            raise AgentContractError(f"issue {j} is not an object")
        detail = item.get("detail")
        if not isinstance(detail, str) or not detail.strip():
            raise AgentContractError(f"issue {j} has a missing/empty detail")
        kind_val = item.get("kind")
        kind = kind_val.strip() if isinstance(kind_val, str) and kind_val.strip() else "unspecified"
        index_val = item.get("index")
        # bool is a subclass of int — exclude it so `true` isn't read as index 1.
        index = index_val if isinstance(index_val, int) and not isinstance(index_val, bool) else None
        issues.append(CritiqueIssue(index=index, kind=kind, detail=detail.strip()))
    return Critique(passed=False, issues=issues)


def summarize(
    items: list[FeedItem], n: int, model: str, *, language: str = DEFAULT_LANGUAGE
) -> Digest:
    """Summarize the top `n` items into a structured Digest via the Agent SDK."""
    if not items:
        return Digest(items=[])

    prompt = _build_prompt(items, n)
    # The model call (and its auth-remediation guidance) lives in the llm seam.
    raw = complete(prompt, system_prompt=summary_system_prompt(language), model=model)

    try:
        data = _parse_json_array(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Could not parse the agent's response as a JSON array: {exc}"
        ) from exc

    digest_items = [
        DigestItem(
            title=str(obj.get("title", "")).strip(),
            link=str(obj.get("link", "")).strip(),
            one_line_summary=str(obj.get("one_line_summary", "")).strip(),
        )
        for obj in data
    ]
    return Digest(items=digest_items)


# --- Phase 5 agents (coordinated by orchestrator.build_digest) --------------


def _format_feedback(feedback: Critique) -> str:
    """Render a reviewer Critique as a corrective instruction for a redo."""
    lines = [
        "Your previous digest was REJECTED by a reviewer. Fix these problems and "
        "return the FULL corrected JSON array (same length, same order):"
    ]
    for issue in feedback.issues:
        where = f"item {issue.index}" if issue.index else "overall"
        lines.append(f"- [{where}] {issue.detail}")
    return "\n".join(lines)


def summarize_agent(
    items: list[FeedItem],
    n: int,
    model: str,
    *,
    feedback: Critique | None = None,
    language: str = DEFAULT_LANGUAGE,
    llm: Callable[..., str] = complete,
) -> Digest:
    """Summarizer agent: items (+ optional reviewer feedback) -> validated Digest.

    Reuses the tool-less prompt of `summarize`; on a redo it appends the reviewer's
    critique so the model can correct specific items. The reply is validated by
    `parse_digest` (strict) — a contract violation raises AgentContractError,
    which the orchestrator handles (dirty data never ships). `llm` is injectable
    so the orchestrator/tests can swap the model call without touching this code.
    """
    chosen = items[:n]
    if not chosen:
        return Digest(items=[])
    prompt = _build_prompt(items, n)
    if feedback is not None and feedback.issues:
        prompt = f"{prompt}\n\n{_format_feedback(feedback)}"
    raw = llm(prompt, system_prompt=summary_system_prompt(language), model=model)
    return parse_digest(raw, chosen)


def _build_verifier_prompt(digest: Digest, chosen: list[FeedItem]) -> str:
    source = [
        {"index": i, "title": it.title, "summary": it.summary}
        for i, it in enumerate(chosen, start=1)
    ]
    candidate = [
        {"index": i, "title": it.title, "one_line_summary": it.one_line_summary}
        for i, it in enumerate(digest.items, start=1)
    ]
    return (
        "SOURCE items:\n"
        + json.dumps(source, ensure_ascii=False, indent=2)
        + "\n\nCANDIDATE digest:\n"
        + json.dumps(candidate, ensure_ascii=False, indent=2)
        + "\n\nReview each candidate summary against the SOURCE item with the same "
        "index."
    )


def verify_agent(
    digest: Digest,
    items: list[FeedItem],
    model: str,
    *,
    llm: Callable[..., str] = complete,
) -> Critique:
    """Verifier agent: candidate Digest + SOURCE items -> validated Critique.

    Checks each summary against its source item (not by feel). The reply is
    validated by `parse_critique` (strict); a malformed review raises
    AgentContractError, which the orchestrator handles (bounded re-verify). `llm`
    is injectable for model-swap / offline tests.
    """
    if not digest.items:
        return Critique(passed=True, issues=[])  # nothing to review
    chosen = items[: len(digest.items)]  # the source items that were summarized
    prompt = _build_verifier_prompt(digest, chosen)
    raw = llm(prompt, system_prompt=VERIFIER_SYSTEM_PROMPT, model=model)
    return parse_critique(raw)
