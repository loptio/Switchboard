"""Agent module — understanding/summarizing via the Claude Agent SDK.

Contract (Phase 1 brief §4):
    input  = list of FeedItems
    output = a structured Digest: top N items, each {title, link, one_line_summary}

This is the only module that talks to Claude. It runs a single-turn, tool-less
query: the agent receives the items as JSON and returns a JSON array of
summaries. No tools are granted, so the run is deterministic and non-interactive.

Auth: the SDK delegates to the Claude Code CLI, which uses your subscription.
Do NOT set ANTHROPIC_API_KEY (that would bill the paid API).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import anyio
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

from fetch import FeedItem


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


async def _run_query(prompt: str, model: str) -> str:
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        model=model,
        # `tools=[]` makes NO tools available (CLI: --tools "") so the agent can
        # only reply with text. NOTE: `allowed_tools` is just a permission
        # allow-list, not the available set — it does NOT disable tools.
        tools=[],
        permission_mode="bypassPermissions",  # belt-and-suspenders; nothing to permit
        max_turns=3,  # headroom; with no tools the model ends in one turn
        setting_sources=[],  # ignore project/user settings for a clean run
    )

    text_chunks: list[str] = []
    result_text: str | None = None
    error: str | None = None
    # ResultMessage is the terminal message, so let the generator finish
    # naturally; capture errors and raise after the loop rather than mid-
    # iteration (raising inside `async for` aborts the generator mid-run).
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_chunks.append(block.text)
        elif isinstance(message, ResultMessage):
            if message.is_error:
                error = str(message.result or message.errors)
            else:
                result_text = message.result

    if error is not None:
        raise RuntimeError(f"Agent run failed: {error}")

    text = (result_text or "".join(text_chunks)).strip()
    if not text:
        raise RuntimeError("Agent returned no text.")
    return text


def _parse_json_array(text: str) -> list[dict]:
    """Extract the JSON array from the agent's reply, tolerating stray fences."""
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON array in agent output: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def summarize(items: list[FeedItem], n: int, model: str) -> Digest:
    """Summarize the top `n` items into a structured Digest via the Agent SDK."""
    if not items:
        return Digest(items=[])

    prompt = _build_prompt(items, n)
    try:
        raw = anyio.run(_run_query, prompt, model)
    except Exception as exc:  # surface a clear, actionable message
        raise RuntimeError(
            f"Agent summarization failed: {exc}\n"
            "If this is an auth error (e.g. 'Not logged in'), authenticate the "
            "Claude Code CLI with your subscription: run `claude`, then /login. "
            "Do NOT set ANTHROPIC_API_KEY (that bills the paid API)."
        ) from exc
    data = _parse_json_array(raw)

    digest_items = [
        DigestItem(
            title=str(obj.get("title", "")).strip(),
            link=str(obj.get("link", "")).strip(),
            one_line_summary=str(obj.get("one_line_summary", "")).strip(),
        )
        for obj in data
    ]
    return Digest(items=digest_items)
