"""Agent module — understanding/summarizing via the Claude Agent SDK.

Contract (Phase 1 brief §4):
    input  = list of FeedItems
    output = a structured Digest: top N items, each {title, link, one_line_summary}

The actual model call lives in the `llm.complete` seam (the only module that
imports the SDK); this module builds prompts and validates replies against their
contracts. `summarize` receives the items as JSON and returns a JSON array of
summaries; no tools are granted, so the run is deterministic and non-interactive.

Auth: the SDK delegates to the Claude Code CLI, which uses your subscription.
Do NOT set ANTHROPIC_API_KEY (that would bill the paid API).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from fetch import FeedItem
from llm import complete


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


# Canonical critique issue kinds (the verifier prompt asks for these). NOT
# enforced as an enum: an unknown kind is kept verbatim — `detail` carries the
# actionable content and `index` points at the offending digest item. Note that
# `fabricated_link`/`title_mismatch` are structurally prevented by parse_digest
# (title/link come from the source), so in practice the verifier's job is
# summary faithfulness; they remain here as a vocabulary + unit-tested backstop.
ISSUE_KINDS = (
    "hallucination",       # summary states something not in the source
    "summary_inaccurate",  # summary distorts / misrepresents the source
    "missing_item",        # a source item that should be covered is absent
    "fabricated_link",     # link not present in the source
    "title_mismatch",      # title not matching the source
    "format",              # structural / format problem
)


@dataclass(frozen=True)
class CritiqueIssue:
    index: int | None  # 1-based digest item the issue refers to, or None if global
    kind: str          # ideally one of ISSUE_KINDS
    detail: str         # explanation; fed back to the summarizer on redo


@dataclass(frozen=True)
class Critique:
    passed: bool
    issues: list[CritiqueIssue]


SYSTEM_PROMPT = (
    "You are a precise news-digest assistant. You receive a JSON list of RSS "
    "feed items (title, link, summary). For each item, write one concise "
    "one-sentence summary in the same language as the item. "
    "Respond with ONLY a JSON array; each element is an object "
    '{"title": str, "link": str, "one_line_summary": str}. '
    "Preserve each given title and link verbatim. Keep the input order. "
    "No prose, no markdown, no code fences."
)


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


def summarize(items: list[FeedItem], n: int, model: str) -> Digest:
    """Summarize the top `n` items into a structured Digest via the Agent SDK."""
    if not items:
        return Digest(items=[])

    prompt = _build_prompt(items, n)
    # The model call (and its auth-remediation guidance) lives in the llm seam.
    raw = complete(prompt, system_prompt=SYSTEM_PROMPT, model=model)

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
