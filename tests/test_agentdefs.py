"""Offline tests for agents-as-data (Phase 7, Unit 1).

AgentDef shape, the str.replace renderer (literal JSON braces survive), and that
the system-prompt TEXT now living in `agentdefs` is the source of truth the agent
functions render from (no behaviour drift — the byte-for-byte pinning lives in
test_agents/test_brief_agent, which call the agent functions directly).
"""

import agent
import brief_agent
from agentdefs import AGENT_DEFS, ISSUE_KINDS, AgentDef, render
from config import DEFAULT_LANGUAGE


def test_agent_defs_cover_the_five_agents():
    assert set(AGENT_DEFS) == {
        "summarize",
        "verify",
        "filter",
        "summarize_item",
        "perspective",
    }
    for key, adef in AGENT_DEFS.items():
        assert isinstance(adef, AgentDef)
        assert adef.id == key
        assert adef.model is None  # every agent inherits the workflow/config model
        assert adef.system_prompt and isinstance(adef.system_prompt, str)
        assert adef.prompt_builder_ref and adef.parser_ref


def test_params_hold_language_only_for_language_aware_agents():
    assert AGENT_DEFS["summarize"].params == {"language": DEFAULT_LANGUAGE}
    assert AGENT_DEFS["summarize_item"].params == {"language": DEFAULT_LANGUAGE}
    assert AGENT_DEFS["perspective"].params == {"language": DEFAULT_LANGUAGE}
    # the filter is language-agnostic; verify is a judgment — no language param.
    assert AGENT_DEFS["filter"].params == {}
    assert AGENT_DEFS["verify"].params == {}


# --- render: str.replace, not str.format (literal braces survive) -----------


def test_render_substitutes_only_supplied_markers():
    assert render("hi {language}!", language="X") == "hi X!"
    assert render("{stance}/{language}", stance="商业", language="EN") == "商业/EN"
    # an unsupplied marker is left untouched (no KeyError, unlike str.format)
    assert render("{stance} {language}", language="EN") == "{stance} EN"


def test_render_leaves_literal_json_braces_intact():
    # the whole reason for str.replace: a format-string would choke on these.
    tmpl = 'reply {"passed": bool, "k": [1]} in {language}'
    assert render(tmpl, language="EN") == 'reply {"passed": bool, "k": [1]} in EN'


# --- the data is the source of truth the agent code renders from ------------


def test_summarize_prompt_is_rendered_from_the_agentdef():
    # the public function delegates to the AgentDef text + render()
    assert agent.summary_system_prompt("English") == render(
        AGENT_DEFS["summarize"].system_prompt, language="English"
    )
    assert "English" in agent.summary_system_prompt("English")


def test_constant_prompts_are_the_agentdef_text_verbatim():
    assert agent.VERIFIER_SYSTEM_PROMPT == AGENT_DEFS["verify"].system_prompt
    assert brief_agent.FILTER_SYSTEM_PROMPT == AGENT_DEFS["filter"].system_prompt
    # the verifier prompt still enumerates every issue kind (vocabulary backstop)
    for kind in ISSUE_KINDS:
        assert kind in AGENT_DEFS["verify"].system_prompt


def test_brief_prompts_are_rendered_from_the_agentdef():
    assert brief_agent._summary_system_prompt("繁體中文") == render(
        AGENT_DEFS["summarize_item"].system_prompt, language="繁體中文"
    )
    assert brief_agent._perspective_system_prompt("政策", "English") == render(
        AGENT_DEFS["perspective"].system_prompt, stance="政策", language="English"
    )
    # stance + language both interpolated
    p = brief_agent._perspective_system_prompt("政策", "English")
    assert "政策" in p and "English" in p
