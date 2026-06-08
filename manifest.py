"""Component palette (Phase 8) — the registered building blocks, as PURE DATA.

The web synthesizer needs to know which components exist (to populate the
structured-form dropdowns) and the validator needs it (to reject a def that
references an unregistered component). But the web tier must NOT import the real
registries: `components.py` pulls the Agent SDK and `engine` pulls langgraph (see
tests/test_api_no_sdk.py). So this module DECLARES the palette as plain strings
the web can safely import, and a worker-side test (tests/test_manifest.py) pins it
EQUAL to the live registries (components.NODE_HANDLERS / PREDICATES / COMPOSERS /
AGENTS / PROMPT_BUILDERS / PARSERS / SOURCES / RENDERERS), so the data can never
drift from the code.

This is the Phase 7 data/code boundary (α) made browseable: the synthesizer (and a
future meta-agent) creates DATA that recombines these names; it cannot invent a
component — that is code a human adds to the registry first.
"""

from __future__ import annotations

# End-of-graph sentinel a route/`next` may target (mirrors workflows.END, kept here
# so the validator can stay a pure-data leaf the web imports without workflows).
END = "__end__"

# Graph glue (registered in orchestrator.py / brief_orchestrator.py at import).
NODE_HANDLERS = (
    "digest_summarize",
    "digest_verify",
    "digest_accept_last",
    "digest_finalize_gate",
    "digest_human_review",
    "brief_filter",
    "brief_summary",
    "brief_perspective",
)
PREDICATES = (
    "digest_route_after_summarize",
    "digest_route_after_verify",
    "digest_route_after_finalize_gate",
    "digest_route_after_human_review",
)
COMPOSERS = ("perspective_value", "brief_item", "assemble_brief")

# Agents + their procedural pieces (registered in components.py at import).
AGENTS = ("summarize", "verify", "filter", "summarize_item", "perspective")
PROMPT_BUILDERS = (
    "digest_summary_prompt",
    "digest_verify_prompt",
    "brief_filter_prompt",
    "brief_summary_prompt",
    "brief_perspective_prompt",
)
PARSERS = (
    "parse_digest",
    "parse_critique",
    "parse_filter",
    "parse_summary",
    "parse_perspective",
)

# Intake / delivery (declared by name; runner-driven this phase — Phase 7 D3).
SOURCES = ("hn_feed", "multi_rss")
RENDERERS = ("digest", "brief")

# Node kinds + which fields each uses (drives the structured form + validation).
NODE_KINDS = {
    "step": {
        "requires": ["handler_ref"],
        "optional": ["agent_ref", "config_key"],
        "edge": "next|branch",
    },
    "human_review": {
        "requires": ["handler_ref"],
        "optional": ["agent_ref", "config_key"],
        "edge": "next|branch",
    },
    "fan_out": {
        "requires": ["over", "element_key", "into", "body"],
        "optional": ["collect_ref"],
        "edge": "next",
    },
    "gather": {"requires": ["compose_ref", "into"], "edge": "next"},
}

# The two runnable families. A WorkflowDef's `output_ref` selects the worker harness
# (state schema + intake + delivery + finalize); `source_ref` must match. A
# genuinely new family = new CODE (handler + state schema + renderer + source), not
# data — the synthesizer / meta-agent limit. `review` = whether the family supports
# the human-review gate (digest only).
FAMILIES = (
    {"id": "digest", "source": "hn_feed", "output": "digest", "review": True, "state": "digest"},
    {"id": "brief", "source": "multi_rss", "output": "brief", "review": False, "state": "brief"},
)


def build_manifest() -> dict:
    """The palette as a plain dict — for the web (GET /components) and the validator."""
    return {
        "node_kinds": {k: dict(v) for k, v in NODE_KINDS.items()},
        "node_handlers": list(NODE_HANDLERS),
        "predicates": list(PREDICATES),
        "composers": list(COMPOSERS),
        "agents": list(AGENTS),
        "prompt_builders": list(PROMPT_BUILDERS),
        "parsers": list(PARSERS),
        "sources": list(SOURCES),
        "renderers": list(RENDERERS),
        "families": [dict(f) for f in FAMILIES],
        "end": END,
    }
