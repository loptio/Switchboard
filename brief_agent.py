"""Brief workflow agents + output contracts (Phase 6, Unit 2).

The second real workflow's agents. Like the digest's `agent.py`, this module only
builds prompts, calls the model via the `llm.complete` seam (tools=[], no SDK
imported here beyond the seam), and validates replies against their contracts. The
control flow lives in `brief_orchestrator.build_brief` (a LangGraph graph) — this
module holds no orchestration.

Three agents, each a focused model call:
- `filter_agent`     : many candidate items -> the indices worth keeping (<= cap).
                       A single critical "real value vs noise/hype" judgment.
- `summarize_item_agent`: one item -> a concise summary (grounded in that item).
- `perspective_agent`: one item + one stance -> a Perspective. Each stance is a
                       SEPARATE call with its own system prompt (fresh context,
                       un-anchored) — the multi-agent fan-out the brief is built to
                       demonstrate.

There is deliberately NO verifier agent (unlike the digest): faithfulness is
enforced by the prompts ("base it ONLY on this item; do not fabricate") plus
provenance that the model cannot rewrite — title/link/source/domain are taken from
the SourceItem, never from the model (same anti-fabrication rule as the digest).

Contracts:
    Perspective: {stance, take}
    BriefItem:   {title, link, source, domain, summary, perspectives: [Perspective]}
    Brief:       {date, items: [BriefItem]}

Auth: the seam delegates to the Claude Code CLI subscription. Do NOT set
ANTHROPIC_API_KEY (that bills the paid API).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from agent import AgentContractError  # shared contract-violation type
from config import DEFAULT_LANGUAGE
from llm import complete
from sources import SourceItem

# --- config (data; brief §3/§11, adjustable) --------------------------------
STANCES: tuple[str, ...] = ("商业", "政策", "技术")  # the 3 fixed perspectives
KEEP_CAP = 8                # keep at most this many items after filtering (§3)
FILTER_SUMMARY_CHARS = 240  # the filter sees only a SHORT summary, never full text


# --- contracts --------------------------------------------------------------
@dataclass(frozen=True)
class Perspective:
    stance: str  # which lens (e.g. 商业/政策/技术) — set by us, not the model
    take: str    # the model's analysis from that lens


@dataclass(frozen=True)
class BriefItem:
    title: str
    link: str
    source: str
    domain: str
    summary: str
    perspectives: list[Perspective]


@dataclass(frozen=True)
class Brief:
    date: str
    items: list[BriefItem]


# --- small JSON helper (mirrors agent.py; kept local to avoid private imports) ---
def _parse_json_object(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object in agent output: {text[:200]!r}")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError(f"Agent output is not a JSON object: {text[:200]!r}")
    return data


def _short(text: str, limit: int = FILTER_SUMMARY_CHARS) -> str:
    """Collapse whitespace and truncate to a short summary for the filter input."""
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit].rstrip() + "…"


# --- filter agent -----------------------------------------------------------
FILTER_SYSTEM_PROMPT = (
    "You are a sharp editor curating a high-signal, cross-domain briefing. From a "
    "list of candidate items you keep ONLY those of genuine, lasting value and drop "
    "noise: hype, clickbait, pure promotion, rumor, and low-information filler. You "
    "judge each item ONLY from its given title, source and short summary — never "
    "outside knowledge. You favor covering several domains over piling up items from "
    'one. Respond with ONLY a JSON object {"keep": [<1-based index>, ...]} listing '
    "the kept indices, best first. No prose, no markdown, no code fences."
)


def _build_filter_prompt(items: list[SourceItem], keep_cap: int) -> str:
    payload = [
        {
            "index": i,
            "title": it.title,
            "source": it.source,
            "domain": it.domain,
            "summary": _short(it.text),
        }
        for i, it in enumerate(items, start=1)
    ]
    return (
        f"Here are {len(items)} candidate items. Select at most {keep_cap} of genuine "
        "value, ranked best first, dropping noise/hype/low-information items. Prefer "
        "cross-domain coverage.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + f'\n\nReturn ONLY {{"keep": [...]}} with at most {keep_cap} 1-based indices, '
        "best first."
    )


def parse_filter(raw: str, n_candidates: int, keep_cap: int) -> list[int]:
    """Validate a filter reply -> a clean list of 1-based indices to keep.

    Strict on shape (must be a JSON object with a "keep" array) so a malformed
    reply raises AgentContractError (the orchestrator retries / fails). Lenient on
    content: out-of-range, duplicate, and non-int (incl. bool) entries are dropped,
    order is preserved (the model's ranking), and the result is truncated to
    keep_cap. An EMPTY keep list is valid — it means "everything was noise".
    """
    try:
        obj = _parse_json_object(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise AgentContractError(f"filter reply is not a JSON object: {exc}") from exc
    keep = obj.get("keep")
    if not isinstance(keep, list):
        raise AgentContractError("filter reply must have a 'keep' array")
    seen: set[int] = set()
    out: list[int] = []
    for v in keep:
        # bool is a subclass of int — exclude it so `true` isn't read as index 1.
        if isinstance(v, bool) or not isinstance(v, int):
            continue
        if 1 <= v <= n_candidates and v not in seen:
            seen.add(v)
            out.append(v)
        if len(out) >= keep_cap:
            break
    return out


def filter_agent(
    items: list[SourceItem],
    model: str,
    *,
    keep_cap: int = KEEP_CAP,
    llm: Callable[..., str] = complete,
) -> list[SourceItem]:
    """Filter agent: candidate items -> the kept SourceItems (<= keep_cap).

    The model only ever sees title/source/domain/short-summary (never full text),
    and returns indices; we map indices back to the original SourceItems, so the
    kept items keep their exact provenance. `llm` is injectable for offline tests.
    """
    if not items:
        return []
    prompt = _build_filter_prompt(items, keep_cap)
    raw = llm(prompt, system_prompt=FILTER_SYSTEM_PROMPT, model=model)
    indices = parse_filter(raw, len(items), keep_cap)
    return [items[i - 1] for i in indices]


# --- summary agent ----------------------------------------------------------
def _summary_system_prompt(language: str) -> str:
    return (
        "You summarize a single news item in one or two concise sentences, written "
        f"in {language} — write the summary in {language} even if the source is in "
        "another language. Base the summary ONLY on the provided title and text — do "
        "not add facts, numbers, or claims that are not present (no fabrication). "
        "Respond with ONLY the summary sentence(s): no preamble, no JSON, no markdown."
    )


def _build_summary_prompt(item: SourceItem) -> str:
    return "Summarize this item:\n\n" + json.dumps(
        {"title": item.title, "source": item.source, "text": item.text},
        ensure_ascii=False,
        indent=2,
    )


def summarize_item_agent(
    item: SourceItem,
    model: str,
    *,
    language: str = DEFAULT_LANGUAGE,
    llm: Callable[..., str] = complete,
) -> str:
    """Summary agent: one item -> a concise, grounded summary string in `language`."""
    raw = llm(
        _build_summary_prompt(item),
        system_prompt=_summary_system_prompt(language),
        model=model,
    )
    summary = raw.strip()
    if not summary:
        raise AgentContractError("summary agent returned an empty summary")
    return summary


# --- perspective agent (one call per stance) --------------------------------
def _perspective_system_prompt(stance: str, language: str) -> str:
    return (
        "You are a sharp analyst. Analyze the given news item strictly through the "
        f"lens of its {stance} implications. Give ONE specific, insightful take of two "
        f"to three sentences, written in {language} (even if the source is in another "
        "language). Ground every claim in THIS item's content — do not fabricate facts "
        "beyond it (you may reason about implications, but tie them to the item). "
        "Respond with ONLY your take: no preamble, no JSON, no markdown."
    )


def _build_perspective_prompt(item: SourceItem) -> str:
    return "Item:\n\n" + json.dumps(
        {"title": item.title, "source": item.source, "domain": item.domain, "text": item.text},
        ensure_ascii=False,
        indent=2,
    )


def perspective_agent(
    item: SourceItem,
    stance: str,
    model: str,
    *,
    language: str = DEFAULT_LANGUAGE,
    llm: Callable[..., str] = complete,
) -> Perspective:
    """Perspective agent: one item + one stance -> a Perspective (take in `language`).

    Each stance is a separate call with its own system prompt (fresh, un-anchored
    context). `stance` is set by us on the returned Perspective — the model writes
    only the `take`, never which stance this is.
    """
    raw = llm(
        _build_perspective_prompt(item),
        system_prompt=_perspective_system_prompt(stance, language),
        model=model,
    )
    take = raw.strip()
    if not take:
        raise AgentContractError(f"{stance} perspective agent returned an empty take")
    return Perspective(stance=stance, take=take)
