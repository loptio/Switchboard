"""Offline tests for the component registry (Phase 7, Unit 1).

The registry catalogues the CODE components by name (parsers / prompt-builders /
agents / sources / renderers) so declarative AgentDef/WorkflowDef data can
reference them. These tests pin that the names resolve to the real functions and
that every AgentDef reference is actually satisfied by the registry.
"""

import pytest

import agent
import brief_agent
import components
import fetch
import output
import sources
from agentdefs import AGENT_DEFS


def test_parsers_resolve_to_the_real_functions():
    assert components.PARSERS["parse_digest"] is agent.parse_digest
    assert components.PARSERS["parse_critique"] is agent.parse_critique
    assert components.PARSERS["parse_filter"] is brief_agent.parse_filter
    assert components.PARSERS["parse_summary"] is brief_agent.parse_summary
    assert components.PARSERS["parse_perspective"] is brief_agent.parse_perspective


def test_prompt_builders_and_agents_resolve():
    assert components.PROMPT_BUILDERS["digest_summary_prompt"] is agent._build_prompt
    assert components.PROMPT_BUILDERS["digest_verify_prompt"] is agent._build_verifier_prompt
    assert components.AGENTS["summarize"] is agent.summarize_agent
    assert components.AGENTS["verify"] is agent.verify_agent
    assert components.AGENTS["filter"] is brief_agent.filter_agent
    assert components.AGENTS["summarize_item"] is brief_agent.summarize_item_agent
    assert components.AGENTS["perspective"] is brief_agent.perspective_agent


def test_sources_and_renderers_registered_by_name():
    assert components.SOURCES["hn_feed"] is fetch.fetch_feed
    assert components.SOURCES["multi_rss"] is sources.gather_sources
    assert components.RENDERERS["digest"] is output.render_markdown
    assert components.RENDERERS["brief"] is output.render_brief_markdown


def test_every_agentdef_ref_is_satisfied_by_the_registry():
    # the data/code contract: each AgentDef's refs must resolve in the registry.
    for adef in AGENT_DEFS.values():
        assert adef.prompt_builder_ref in components.PROMPT_BUILDERS, adef.id
        assert adef.parser_ref in components.PARSERS, adef.id
        assert adef.id in components.AGENTS  # the assembled callable exists too


def test_register_rejects_conflicting_duplicate_but_allows_idempotent():
    reg: dict = {}
    f = lambda: 1  # noqa: E731
    components.register(reg, "x", f)
    components.register(reg, "x", f)  # same object -> idempotent, no error
    assert reg["x"] is f
    with pytest.raises(ValueError, match="already registered"):
        components.register(reg, "x", lambda: 2)
