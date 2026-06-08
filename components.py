"""Component registry (Phase 7, Unit 1) — the code components, referenced by name.

The data/code line (brief decision A): prompt/model/params are DATA (`agentdefs`,
`workflows`); the procedural pieces are CODE, catalogued HERE by name so a
declarative `AgentDef`/`WorkflowDef` (and, later, a meta-agent) can reference them
without importing Python symbols:

- `PROMPT_BUILDERS` — build a user message from structured items (+ params)
- `PARSERS`         — validate a model reply against its contract (some take context)
- `AGENTS`          — the assembled agent callables (the injectable default fns)
- `SOURCES`         — intake callables (digest: a feed; brief: multi-source gather)
- `RENDERERS`       — render a result contract to markdown
- `NODE_HANDLERS` / `PREDICATES` / `COMPOSERS` — added in Unit 2/3 (graph glue:
  state<->agent bindings, conditional routing, result assembly) for the engine.

This module is WORKER-SIDE (it imports the agents, which import the llm seam → the
Agent SDK). The web tier must never import it (see tests/test_api_no_sdk.py). It is
imported by the generic engine / orchestrators, never the reverse, so the import
graph stays acyclic: components -> agent/brief_agent/... ; engine -> components.
"""

from __future__ import annotations

import agent
import brief_agent
from fetch import fetch_feed
from output import render_brief_markdown, render_markdown
from sources import gather_sources

# --- prompt builders (build the user message from structured input) ---------
PROMPT_BUILDERS = {
    "digest_summary_prompt": agent._build_prompt,            # (items, n) -> str
    "digest_verify_prompt": agent._build_verifier_prompt,    # (digest, chosen) -> str
    "brief_filter_prompt": brief_agent._build_filter_prompt,  # (items, keep_cap) -> str
    "brief_summary_prompt": brief_agent._build_summary_prompt,  # (item) -> str
    "brief_perspective_prompt": brief_agent._build_perspective_prompt,  # (item) -> str
}

# --- parsers (validate a reply against a contract; some take context) -------
PARSERS = {
    "parse_digest": agent.parse_digest,        # (raw, chosen) -> Digest
    "parse_critique": agent.parse_critique,    # (raw) -> Critique
    "parse_filter": brief_agent.parse_filter,  # (raw, n_candidates, keep_cap) -> list[int]
    "parse_summary": brief_agent.parse_summary,  # (raw) -> str
    "parse_perspective": brief_agent.parse_perspective,  # (raw, stance) -> Perspective
}

# --- assembled agent callables (the injectable default fns; AgentDef.id -> fn) ---
AGENTS = {
    "summarize": agent.summarize_agent,
    "verify": agent.verify_agent,
    "filter": brief_agent.filter_agent,
    "summarize_item": brief_agent.summarize_item_agent,
    "perspective": brief_agent.perspective_agent,
}

# --- sources (intake) -------------------------------------------------------
# Declared + registered by name (brief §4). NOTE: the runner still drives intake
# via its own module-level names this phase (test_runner/test_handoff monkeypatch
# runner.fetch_feed / runner.gather_sources), so these are the forward-compat
# catalogue, not yet the runtime driver — see the Phase 7 design note D3.
SOURCES = {
    "hn_feed": fetch_feed,         # (url) -> list[FeedItem]
    "multi_rss": gather_sources,   # () -> list[SourceItem]
}

# --- renderers (result contract -> markdown) --------------------------------
# Likewise declared/registered; the runner drives delivery (render+write+save+email).
RENDERERS = {
    "digest": render_markdown,        # (digest, feed_url, day) -> str
    "brief": render_brief_markdown,   # (brief) -> str
}

# --- graph glue (populated in Unit 2/3 by the orchestrators) ----------------
# The engine looks these up by name when compiling a WorkflowDef. Kept as plain
# dicts the orchestrator modules register into at import time, so the engine never
# needs to import the orchestrators (no cycle).
NODE_HANDLERS: dict = {}   # name -> (state, config) -> state delta
PREDICATES: dict = {}      # name -> (state) -> branch label
COMPOSERS: dict = {}       # name -> assemble result (BriefItem / Brief / ...)


def register(registry: dict, name: str, fn):
    """Register `fn` under `name`, rejecting an accidental duplicate (a meta-agent
    or a careless edit overwriting a component should fail loudly, not silently)."""
    if name in registry and registry[name] is not fn:
        raise ValueError(f"component {name!r} already registered to a different object")
    registry[name] = fn
    return fn
