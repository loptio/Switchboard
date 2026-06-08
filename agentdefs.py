"""Agent definitions as data (Phase 7, Unit 1) — the blueprint's "定义即一等数据".

Each agent's *declarative* parts — its system prompt, default model, and tunable
params — live here as data, not buried in agent code. The *procedural* parts
(building the user message from structured items, parsing/validating the reply
against a contract) stay as code, referenced BY NAME (`prompt_builder_ref` /
`parser_ref`) through the component registry (`components.py`). This is the
data/code line from the Phase 7 brief (decision A): prompt/model/params = data;
parser/builder/source/renderer = code by name.

System prompts are templates with `{language}` / `{stance}` markers, rendered by
plain str.replace (`render`) so literal JSON braces in the text (e.g.
`{"passed": bool}`) are never touched — a format-string would choke on them.

This module imports nothing from `agent`/`brief_agent`: it is a pure-data leaf so
the import graph stays acyclic (components -> agent/brief_agent -> agentdefs).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config import DEFAULT_LANGUAGE

# Canonical critique issue kinds the verifier prompt enumerates. This is part of
# the verifier's *prompt vocabulary* (data), so it lives here; `agent.py`
# re-exports it for back-compat. NOT enforced as an enum — an unknown kind is kept
# verbatim by `parse_critique` (the parser stays lenient).
ISSUE_KINDS = (
    "hallucination",       # summary states something not in the source
    "summary_inaccurate",  # summary distorts / misrepresents the source
    "missing_item",        # a source item that should be covered is absent
    "fabricated_link",     # link not present in the source
    "title_mismatch",      # title not matching the source
    "format",              # structural / format problem
)


@dataclass(frozen=True)
class AgentDef:
    """One agent's declarative definition.

    - `system_prompt`: DATA — a template (with `{language}`/`{stance}` markers).
    - `prompt_builder_ref` / `parser_ref`: names of CODE components in the registry
      (the user-message builder and the reply validator).
    - `model`: DATA — default model; `None` inherits the workflow/config model
      (the future per-agent provider/model routing seam, blueprint §5.5).
    - `params`: DATA — tunable defaults that used to be hardcoded constants
      (e.g. the output `language`).
    """

    id: str
    system_prompt: str
    prompt_builder_ref: str
    parser_ref: str
    model: str | None = None
    params: dict = field(default_factory=dict)


def render(template: str, **params: str) -> str:
    """Render a system-prompt template by replacing `{name}` markers with values.

    Uses str.replace (NOT str.format) so literal `{...}` JSON in the prompt text is
    left intact. Only the markers actually supplied are substituted; any others are
    left as-is.
    """
    text = template
    for name, value in params.items():
        text = text.replace("{" + name + "}", value)
    return text


# --- prompt templates (data) -----------------------------------------------
# These are the verbatim Phase 1/5/6 system prompts, with the interpolated
# `language`/`stance` turned into `{...}` markers. `tests/test_agentdefs.py` pins
# them byte-for-byte against the pre-Phase-7 strings (no behaviour drift).

_SUMMARIZE_SYSTEM_PROMPT = (
    "You are a precise news-digest assistant. You receive a JSON list of RSS "
    "feed items (title, link, summary). For each item, write one concise "
    "one-sentence summary in {language} — write it in {language} even if the "
    "source item is in another language. "
    "Respond with ONLY a JSON array; each element is an object "
    '{"title": str, "link": str, "one_line_summary": str}. '
    "Preserve each given title and link verbatim. Keep the input order. "
    "No prose, no markdown, no code fences."
)

_VERIFY_SYSTEM_PROMPT = (
    "You are a meticulous fact-checking reviewer for a news digest. You receive "
    "the SOURCE feed items and a CANDIDATE digest (one one-sentence summary per "
    "item, same order). For EACH summary, check it ONLY against its source item "
    "(title + summary text) — never your own world knowledge. Flag a summary that "
    "states something the source does not support (hallucination), distorts or "
    "misrepresents the source (summary_inaccurate), or drops the source's main "
    "point (missing_item). "
    'Respond with ONLY a JSON object: {"passed": bool, "issues": [{"index": int, '
    '"kind": str, "detail": str}]}. "index" is the 1-based item number; "kind" is '
    'one of ' + ", ".join(ISSUE_KINDS) + '; "detail" is a specific, actionable '
    'reason. If every summary is faithful, return {"passed": true, "issues": []}; '
    "otherwise set passed=false and list each problem. No prose, no markdown, no "
    "code fences."
)

_FILTER_SYSTEM_PROMPT = (
    "You are a sharp editor curating a high-signal, cross-domain briefing. From a "
    "list of candidate items you keep ONLY those of genuine, lasting value and drop "
    "noise: hype, clickbait, pure promotion, rumor, and low-information filler. You "
    "judge each item ONLY from its given title, source and short summary — never "
    "outside knowledge. You favor covering several domains over piling up items from "
    'one. Respond with ONLY a JSON object {"keep": [<1-based index>, ...]} listing '
    "the kept indices, best first. No prose, no markdown, no code fences."
)

_SUMMARIZE_ITEM_SYSTEM_PROMPT = (
    "You summarize a single news item in one or two concise sentences, written "
    "in {language} — write the summary in {language} even if the source is in "
    "another language. Base the summary ONLY on the provided title and text — do "
    "not add facts, numbers, or claims that are not present (no fabrication). "
    "Respond with ONLY the summary sentence(s): no preamble, no JSON, no markdown."
)

_PERSPECTIVE_SYSTEM_PROMPT = (
    "You are a sharp analyst. Analyze the given news item strictly through the "
    "lens of its {stance} implications. Give ONE specific, insightful take of two "
    "to three sentences, written in {language} (even if the source is in another "
    "language). Ground every claim in THIS item's content — do not fabricate facts "
    "beyond it (you may reason about implications, but tie them to the item). "
    "Respond with ONLY your take: no preamble, no JSON, no markdown."
)


# --- the five agent definitions --------------------------------------------
# `model=None` everywhere for now: every agent inherits the workflow/config model
# (no per-agent override yet — the field is the seam, blueprint §5.5). `params`
# holds only the output `language` for the language-aware agents; workflow-level
# knobs (keep_cap, stances, max_redos) live on the WorkflowDef (Unit 2/3), not here.
AGENT_DEFS: dict[str, AgentDef] = {
    "summarize": AgentDef(
        id="summarize",
        system_prompt=_SUMMARIZE_SYSTEM_PROMPT,
        prompt_builder_ref="digest_summary_prompt",
        parser_ref="parse_digest",
        params={"language": DEFAULT_LANGUAGE},
    ),
    "verify": AgentDef(
        id="verify",
        system_prompt=_VERIFY_SYSTEM_PROMPT,
        prompt_builder_ref="digest_verify_prompt",
        parser_ref="parse_critique",
    ),
    "filter": AgentDef(
        id="filter",
        system_prompt=_FILTER_SYSTEM_PROMPT,
        prompt_builder_ref="brief_filter_prompt",
        parser_ref="parse_filter",
    ),
    "summarize_item": AgentDef(
        id="summarize_item",
        system_prompt=_SUMMARIZE_ITEM_SYSTEM_PROMPT,
        prompt_builder_ref="brief_summary_prompt",
        parser_ref="parse_summary",
        params={"language": DEFAULT_LANGUAGE},
    ),
    "perspective": AgentDef(
        id="perspective",
        system_prompt=_PERSPECTIVE_SYSTEM_PROMPT,
        prompt_builder_ref="brief_perspective_prompt",
        parser_ref="parse_perspective",
        params={"language": DEFAULT_LANGUAGE},
    ),
}
