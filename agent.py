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
