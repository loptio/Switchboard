"""Meta agent (Phase 9) — drafts workflow/agent definition PROPOSALS, as pure data.

The meta-agent is the third writer of the SAME definition data that code (Phase 7)
and the web synthesizer (Phase 8) write — and like them it writes DATA, never code.
Its creative space is the registered palette (`manifest.build_manifest()`): it
recombines node handlers / predicates / composers / agents and tunes prompts and
params. It cannot invent a component, a node kind, a source, or a family — those
are code a human registers first (the Phase 7 data/code line; the Phase 9 brief's
palette hard boundary).

Three pieces, mirroring the digest/brief agent modules:
- `build_meta_prompt`   — the user message: request + palette + the built-in defs as
  grounding + (on a redo) the prior proposal, validator errors, human feedback.
- `parse_meta_proposal` — STRICT parser for the reply: one JSON object
  `{"workflow_def": {...}, "agent_defs": [...], "explanation": str}`, tolerant of
  fences/prose around it; AgentContractError on any shape violation. Proposed agent
  defs are normalized: `params` coerced to a dict and `model` FORCED to null —
  per-agent model routing is an unthreaded seam (blueprint §5.5), and a silently
  inert override must never survive review looking meaningful.
- `draft_proposal`      — the agent fn (the `llm.complete` seam, injectable), the
  exact `summarize_agent` shape so orchestrator/tests inject fakes the same way.

`validate_proposal` lives here too: the meta family's deterministic contract check
(no LLM), composing the Phase 8 guards (`defs_validate.validate_workflow_def` /
`validate_agent_def`) with the meta-only rules (new-id-only, runtime-feasible
(builder, parser) pairs, family-consistent handlers). Worker-side module — it pulls
the llm seam, so the web tier never imports it (tests/test_api_no_sdk.py pins it).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

import agentdefs
import defs_validate
import workflows
from agent import AgentContractError
from agentdefs import render
from config import DEFAULT_LANGUAGE
from llm import complete

# New ids minted by a proposal must be sane, lowercase, and short — they become DB
# keys, run.workflow values, and URL path segments.
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")

# Composers exist only for the brief family (per-item / final assembly); a digest
# proposal referencing one would crash on brief-shaped state keys at runtime.
_COMPOSER_FAMILIES = ("brief",)


META_SYSTEM_PROMPT = (
    "You are a workflow synthesizer for a fixed orchestration engine. You receive a "
    "USER REQUEST and a COMPONENT PALETTE — the only building blocks that exist. You "
    "design ONE new workflow definition (pure data recombining palette names) plus, "
    "only when a custom prompt is genuinely needed, new agent definitions that reuse "
    "a built-in agent's (prompt_builder_ref, parser_ref) pair with a new "
    "system_prompt.\n"
    "HARD RULES:\n"
    "- Use ONLY names present in the palette. Never invent a handler, predicate, "
    "composer, parser, prompt builder, source, renderer, or node kind.\n"
    "- output_ref must name a palette family ('digest' or 'brief') and source_ref "
    "must be that family's source. Use only that family's node handlers and "
    "predicates (their names start with the family prefix, e.g. 'brief_'); "
    "composers exist only for the brief family.\n"
    "- The workflow id and every new agent id must be NEW — not in the palette and "
    "not among the EXISTING IDS listed in the prompt — and match "
    "[a-z][a-z0-9_-]{1,39}.\n"
    "- A new agent def: reuse a built-in agent's exact (prompt_builder_ref, "
    "parser_ref) pair, write a complete system_prompt (you may use {language} or "
    "{stance} markers), set model to null and params to an object. Reference it "
    "from a node via agent_ref, copying that node's config_key from the reference "
    "def of the same family.\n"
    "- Every top-level node needs exactly one of 'next' or 'branch'; branch routes "
    'must target node ids or "__end__"; "__end__" must be reachable from the '
    "entry. Nodes inside a fan_out body carry no next/branch.\n"
    "- Tune workflow params (e.g. stances, keep_cap, max_redos) to the request; "
    "keep values JSON-native.\n"
    'Respond with ONLY one JSON object {"workflow_def": {...}, "agent_defs": '
    '[...], "explanation": "..."} — no prose, no markdown, no code fences. '
    "agent_defs may be []. Write the explanation in {language}: say what you "
    "built, which palette pieces you recombined, and why it satisfies the "
    "request.\n"
    "On a redo you also receive your PRIOR PROPOSAL plus VALIDATOR ERRORS and/or "
    "HUMAN FEEDBACK: fix exactly those problems and return the FULL corrected "
    "proposal."
)


def build_meta_prompt(
    request: str,
    palette: dict,
    *,
    existing_workflow_ids: set | frozenset = frozenset(),
    existing_agent_ids: set | frozenset = frozenset(),
    prior: dict | None = None,
    errors: list | None = None,
    feedback: str | None = None,
) -> str:
    """The drafting user message. The palette and the two built-in defs ground the
    model in what exists; existing ids fence off the taken namespace; prior/errors/
    feedback turn a redo into a targeted correction instead of a fresh roll."""
    reference = {
        "digest": workflows.workflow_def_to_dict(workflows.DIGEST_DEF),
        "brief": workflows.workflow_def_to_dict(workflows.BRIEF_DEF),
    }
    sections = [
        f"USER REQUEST:\n{request}",
        "COMPONENT PALETTE (the only allowed names):\n"
        + json.dumps(palette, ensure_ascii=False, indent=2),
        "EXISTING IDS (taken — your new ids must differ):\n"
        + json.dumps(
            {
                "workflows": sorted(existing_workflow_ids),
                "agents": sorted(existing_agent_ids),
            },
            ensure_ascii=False,
        ),
        "REFERENCE DEFS (the built-in families, serialized — copy their wiring):\n"
        + json.dumps(reference, ensure_ascii=False, indent=2),
    ]
    if prior is not None:
        sections.append(
            "YOUR PRIOR PROPOSAL:\n" + json.dumps(prior, ensure_ascii=False, indent=2)
        )
    if errors:
        sections.append(
            "VALIDATOR ERRORS (fix every one):\n" + "\n".join(f"- {e}" for e in errors)
        )
    if feedback:
        sections.append(f"HUMAN FEEDBACK:\n{feedback}")
    return "\n\n".join(sections)


def parse_meta_proposal(raw: str) -> dict:
    """Validate a drafting reply against the proposal contract → a normalized dict.

    Tolerates fences/prose around the JSON object (same posture as the digest
    parsers). Raises AgentContractError on any shape violation — the orchestrator
    counts it as a failed attempt and feeds it back, dirty data never advances.
    """
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise AgentContractError(f"meta reply has no JSON object: {raw[:200]!r}")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AgentContractError(f"meta reply is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AgentContractError(f"meta reply is not a JSON object: {raw[:200]!r}")

    wf = data.get("workflow_def")
    if not isinstance(wf, dict):
        raise AgentContractError("proposal is missing the 'workflow_def' object")

    raw_agents = data.get("agent_defs", [])
    if not isinstance(raw_agents, list) or not all(
        isinstance(a, dict) for a in raw_agents
    ):
        raise AgentContractError("'agent_defs' must be a list of objects")
    agent_defs = []
    for a in raw_agents:
        norm = dict(a)
        params = norm.get("params")
        norm["params"] = dict(params) if isinstance(params, dict) else {}
        # Per-agent model routing is an unthreaded seam — force the field inert so a
        # reviewer never approves an override that silently does nothing.
        norm["model"] = None
        agent_defs.append(norm)

    explanation = data.get("explanation", "")
    if not isinstance(explanation, str):
        raise AgentContractError("'explanation' must be a string")

    return {"workflow_def": wf, "agent_defs": agent_defs, "explanation": explanation}


def builtin_agent_pairs() -> set:
    """The (prompt_builder_ref, parser_ref) pairs the runtime can actually bind: the
    runner finds an agent's base callable BY this pair (`runner._AGENT_BASE_BY_REFS`),
    so a proposed agent def must reuse one of these or it fails at run time."""
    return {
        (a.prompt_builder_ref, a.parser_ref) for a in agentdefs.AGENT_DEFS.values()
    }


def _walk_nodes(nodes: list):
    """Yield every node dict, recursing into fan_out bodies (defensively typed —
    validate_workflow_def reports non-dict nodes; we just skip them here)."""
    for n in nodes:
        if not isinstance(n, dict):
            continue
        yield n
        body = n.get("body")
        if isinstance(body, list):
            yield from _walk_nodes(body)


def _check_family_consistency(wf: dict, errors: list) -> None:
    """Handlers/predicates must belong to the proposal's family (prefix rule), and
    composers only exist in the brief family. defs_validate can't see this — the
    manifest doesn't family-tag components — but a digest def running brief handlers
    would crash on missing state keys at runtime, so the meta contract enforces it."""
    family = wf.get("output_ref")
    if not isinstance(family, str) or not family:
        return  # validate_workflow_def already reports the missing output_ref
    prefix = f"{family}_"
    for n in _walk_nodes(wf.get("nodes") or []):
        nid = n.get("id", "?")
        handler = n.get("handler_ref")
        if isinstance(handler, str) and not handler.startswith(prefix):
            errors.append(
                f"node {nid!r}: handler {handler!r} is not a {family}-family handler"
            )
        branch = n.get("branch")
        if isinstance(branch, dict):
            pred = branch.get("predicate_ref")
            if isinstance(pred, str) and not pred.startswith(prefix):
                errors.append(
                    f"node {nid!r}: predicate {pred!r} is not a {family}-family predicate"
                )
        if (n.get("collect_ref") or n.get("compose_ref")) and family not in (
            _COMPOSER_FAMILIES
        ):
            errors.append(
                f"node {nid!r}: composers are {'/'.join(_COMPOSER_FAMILIES)}-family "
                f"only, not available to {family!r}"
            )


def validate_proposal(
    proposal: dict,
    *,
    palette: dict,
    existing_workflow_ids: set,
    existing_agent_ids: set,
) -> list[str]:
    """Deterministically validate a proposal. Empty list = valid.

    Composes the Phase 8 guards with the meta-only rules:
    - the workflow id and every proposed agent id are NEW (not built-in, not in DB)
      and well-formed;
    - each proposed agent def passes `validate_agent_def`, reuses a runtime-feasible
      built-in (builder, parser) pair, keeps model null, and is actually referenced;
    - the workflow def passes `validate_workflow_def` against the palette with the
      agents namespace EXTENDED by DB + proposed agent ids (Phase 9 U1);
    - handlers/predicates/composers are family-consistent.
    """
    errors: list[str] = []
    wf = proposal.get("workflow_def")
    if not isinstance(wf, dict):
        return ["proposal has no 'workflow_def' object"]
    agent_defs = proposal.get("agent_defs") or []

    wf_id = wf.get("id")
    if not isinstance(wf_id, str) or not _ID_RE.match(wf_id):
        errors.append(
            f"workflow id {wf_id!r} is invalid (need [a-z][a-z0-9_-]{{1,39}})"
        )
    elif wf_id in existing_workflow_ids:
        errors.append(f"workflow id {wf_id!r} already exists — pick a new id")

    proposed_ids: list[str] = []
    for d in agent_defs:
        pid = d.get("id")
        if not isinstance(pid, str) or not _ID_RE.match(pid):
            errors.append(
                f"agent id {pid!r} is invalid (need [a-z][a-z0-9_-]{{1,39}})"
            )
            continue
        if pid in existing_agent_ids:
            errors.append(f"agent id {pid!r} already exists — pick a new id")
        if pid in proposed_ids:
            errors.append(f"agent id {pid!r} is proposed twice")
        proposed_ids.append(pid)
        for err in defs_validate.validate_agent_def(d, palette):
            errors.append(f"agent {pid!r}: {err}")
        pair = (d.get("prompt_builder_ref"), d.get("parser_ref"))
        if pair not in builtin_agent_pairs():
            errors.append(
                f"agent {pid!r}: (prompt_builder_ref, parser_ref)={pair!r} matches no "
                "built-in agent — the runtime has no base callable for it"
            )
        if d.get("model") is not None:
            errors.append(
                f"agent {pid!r}: 'model' must be null (per-agent model routing is "
                "not wired up yet)"
            )

    # Workflow validation with the agents namespace extended by everything that will
    # resolve at run time: palette built-ins ∪ DB agent defs ∪ this proposal's agents.
    extended = dict(palette)
    extended["agents"] = sorted(
        set(palette.get("agents", [])) | set(existing_agent_ids) | set(proposed_ids)
    )
    errors.extend(defs_validate.validate_workflow_def(wf, extended))

    _check_family_consistency(wf, errors)

    # Each proposed agent must be used — an unreferenced one would persist as an
    # orphan row on approve, which no reviewer asked for.
    referenced = {
        n.get("agent_ref")
        for n in _walk_nodes(wf.get("nodes") or [])
        if n.get("agent_ref")
    }
    for pid in proposed_ids:
        if pid not in referenced:
            errors.append(
                f"agent {pid!r} is proposed but never referenced by the workflow"
            )

    return errors


def draft_proposal(
    request: str,
    *,
    model: str,
    palette: dict,
    existing_workflow_ids: set | frozenset = frozenset(),
    existing_agent_ids: set | frozenset = frozenset(),
    prior: dict | None = None,
    errors: list | None = None,
    feedback: str | None = None,
    language: str = DEFAULT_LANGUAGE,
    llm: Callable[..., str] = complete,
    system_prompt: str | None = None,
) -> dict:
    """Drafting agent: request (+ redo context) -> normalized proposal dict.

    The `summarize_agent` shape: `llm` is the injectable seam (tests pass a fake
    `(prompt, *, system_prompt, model) -> str`); `system_prompt` None means the code
    default; the reply is strictly parsed (AgentContractError on violation)."""
    prompt = build_meta_prompt(
        request,
        palette,
        existing_workflow_ids=existing_workflow_ids,
        existing_agent_ids=existing_agent_ids,
        prior=prior,
        errors=errors,
        feedback=feedback,
    )
    sp = render(
        system_prompt if system_prompt is not None else META_SYSTEM_PROMPT,
        language=language,
    )
    raw = llm(prompt, system_prompt=sp, model=model)
    return parse_meta_proposal(raw)
