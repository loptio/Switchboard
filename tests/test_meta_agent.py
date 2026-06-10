"""Meta agent (Phase 9) — prompt / parser / proposal-validation units, offline.

The drafting seam is faked (`llm=` callable), so no SDK / no key / no network.
`validate_proposal` is exercised against the REAL palette (manifest.build_manifest)
— it is deterministic data-checking, exactly what runs in the worker's validate
node and again in runner._finalize_meta."""

import json

import pytest

import manifest
import workflows
from agent import AgentContractError
from meta_agent import (
    META_SYSTEM_PROMPT,
    build_meta_prompt,
    builtin_agent_pairs,
    draft_proposal,
    parse_meta_proposal,
    validate_proposal,
)

PALETTE = manifest.build_manifest()
BUILTIN_WF_IDS = set(workflows.WORKFLOWS)
BUILTIN_AGENT_IDS = set(PALETTE["agents"])


def _digest_clone(wf_id="meta-made"):
    wf = workflows.workflow_def_to_dict(workflows.DIGEST_DEF)
    wf["id"] = wf_id
    return wf


def _valid_proposal(wf_id="meta-made", agent_id=None):
    """A proposal that must validate: the digest def under a new id, optionally
    rebinding the summarize node to a NEW agent that clones the built-in pair."""
    wf = _digest_clone(wf_id)
    agents = []
    if agent_id is not None:
        wf["nodes"][0]["agent_ref"] = agent_id
        agents.append(
            {
                "id": agent_id,
                "system_prompt": "Summarize sternly in {language}.",
                "prompt_builder_ref": "digest_summary_prompt",
                "parser_ref": "parse_digest",
                "model": None,
                "params": {},
            }
        )
    return {"workflow_def": wf, "agent_defs": agents, "explanation": "一个测试提案"}


# --- parse_meta_proposal -----------------------------------------------------

def test_parse_valid_reply_normalizes():
    reply = json.dumps(
        {
            "workflow_def": {"id": "x"},
            "agent_defs": [{"id": "a", "model": "claude-something", "params": None}],
            "explanation": "ok",
        }
    )
    p = parse_meta_proposal(reply)
    assert p["workflow_def"] == {"id": "x"}
    # model is FORCED inert (per-agent routing unthreaded); params coerced to a dict.
    assert p["agent_defs"][0]["model"] is None
    assert p["agent_defs"][0]["params"] == {}
    assert p["explanation"] == "ok"


def test_parse_tolerates_fences_and_prose():
    reply = 'Here you go:\n```json\n{"workflow_def": {"id": "x"}, "agent_defs": []}\n```\nDone.'
    assert parse_meta_proposal(reply)["workflow_def"] == {"id": "x"}


def test_parse_defaults_agent_defs_and_explanation():
    p = parse_meta_proposal('{"workflow_def": {"id": "x"}}')
    assert p["agent_defs"] == [] and p["explanation"] == ""


@pytest.mark.parametrize(
    "reply",
    [
        "no json here",
        '{"agent_defs": []}',                       # missing workflow_def
        '{"workflow_def": "not an object"}',
        '{"workflow_def": {}, "agent_defs": "x"}',  # agent_defs not a list
        '{"workflow_def": {}, "agent_defs": [1]}',  # agent_defs not objects
        '{"workflow_def": {}, "explanation": 7}',
        '{"workflow_def": {',                       # invalid JSON
    ],
)
def test_parse_rejects_contract_violations(reply):
    with pytest.raises(AgentContractError):
        parse_meta_proposal(reply)


# --- build_meta_prompt -------------------------------------------------------

def test_prompt_carries_request_palette_ids_and_references():
    prompt = build_meta_prompt(
        "做一个三视角简报",
        PALETTE,
        existing_workflow_ids={"news", "brief"},
        existing_agent_ids={"summarize"},
    )
    assert "做一个三视角简报" in prompt
    assert "digest_summarize" in prompt          # palette content
    assert '"news"' in prompt and '"summarize"' in prompt  # taken ids
    assert "REFERENCE DEFS" in prompt and '"multi_rss"' in prompt
    assert "PRIOR PROPOSAL" not in prompt and "VALIDATOR ERRORS" not in prompt


def test_prompt_redo_sections():
    prompt = build_meta_prompt(
        "r",
        PALETTE,
        prior={"workflow_def": {"id": "old"}},
        errors=["bad id"],
        feedback="换个思路",
    )
    assert "YOUR PRIOR PROPOSAL" in prompt and '"old"' in prompt
    assert "VALIDATOR ERRORS" in prompt and "- bad id" in prompt
    assert "HUMAN FEEDBACK:\n换个思路" in prompt


# --- draft_proposal (fake llm seam) -------------------------------------------

def test_draft_proposal_round_trip_with_fake_llm():
    seen = {}

    def fake_llm(prompt, *, system_prompt, model):
        seen.update(prompt=prompt, system_prompt=system_prompt, model=model)
        return json.dumps(_valid_proposal())

    p = draft_proposal(
        "做一个变体",
        model="m",
        palette=PALETTE,
        existing_workflow_ids=BUILTIN_WF_IDS,
        existing_agent_ids=BUILTIN_AGENT_IDS,
        llm=fake_llm,
        language="简体中文",
    )
    assert p["workflow_def"]["id"] == "meta-made"
    assert seen["model"] == "m"
    assert "做一个变体" in seen["prompt"]
    # the {language} marker is rendered into the system prompt
    assert "简体中文" in seen["system_prompt"]
    assert "{language}" not in seen["system_prompt"]
    assert "{language}" in META_SYSTEM_PROMPT  # the template itself keeps the marker


# --- validate_proposal ---------------------------------------------------------

def _validate(p, wf_ids=BUILTIN_WF_IDS, ag_ids=BUILTIN_AGENT_IDS):
    return validate_proposal(
        p, palette=PALETTE, existing_workflow_ids=set(wf_ids), existing_agent_ids=set(ag_ids)
    )


def test_valid_digest_clone_passes():
    assert _validate(_valid_proposal()) == []


def test_valid_proposal_with_new_agent_passes():
    assert _validate(_valid_proposal(agent_id="stern-summarize")) == []


def test_builtin_workflow_id_rejected():
    errors = _validate(_valid_proposal(wf_id="news"))
    assert any("already exists" in e for e in errors)


def test_db_taken_ids_rejected():
    errors = _validate(_valid_proposal(wf_id="taken"), wf_ids=BUILTIN_WF_IDS | {"taken"})
    assert any("already exists" in e for e in errors)
    errors = _validate(
        _valid_proposal(agent_id="dupe"), ag_ids=BUILTIN_AGENT_IDS | {"dupe"}
    )
    assert any("'dupe' already exists" in e for e in errors)


@pytest.mark.parametrize("bad_id", ["Bad", "1x", "-x", "x" * 41, "", None])
def test_malformed_workflow_id_rejected(bad_id):
    p = _valid_proposal()
    p["workflow_def"]["id"] = bad_id
    assert any("invalid" in e for e in _validate(p))


def test_unregistered_handler_rejected():
    p = _valid_proposal()
    p["workflow_def"]["nodes"][0]["handler_ref"] = "made_up_handler"
    assert any("unregistered handler_ref" in e for e in _validate(p))


def test_agent_pair_must_match_a_builtin():
    p = _valid_proposal(agent_id="odd-agent")
    # builder/parser are individually registered but the PAIR matches no built-in →
    # the runtime would have no base callable.
    p["agent_defs"][0]["prompt_builder_ref"] = "digest_summary_prompt"
    p["agent_defs"][0]["parser_ref"] = "parse_critique"
    assert any("no built-in agent" in e for e in _validate(p))
    assert ("digest_summary_prompt", "parse_digest") in builtin_agent_pairs()


def test_agent_model_must_stay_null():
    p = _valid_proposal(agent_id="modeled")
    p["agent_defs"][0]["model"] = "claude-opus-4-8"
    assert any("'model' must be null" in e for e in _validate(p))


def test_unreferenced_proposed_agent_rejected():
    p = _valid_proposal()
    p["agent_defs"].append(
        {
            "id": "orphan",
            "system_prompt": "x",
            "prompt_builder_ref": "digest_summary_prompt",
            "parser_ref": "parse_digest",
            "model": None,
            "params": {},
        }
    )
    assert any("never referenced" in e for e in _validate(p))


def test_cross_family_handler_rejected():
    p = _valid_proposal()
    p["workflow_def"]["nodes"][2]["handler_ref"] = "brief_filter"  # digest family
    errors = _validate(p)
    assert any("not a digest-family handler" in e for e in errors)


def test_composers_are_brief_family_only():
    p = _valid_proposal()
    # Bolt a gather node onto the digest def — composers belong to brief.
    p["workflow_def"]["nodes"].append(
        {"id": "g", "kind": "gather", "compose_ref": "assemble_brief", "into": "r", "next": "__end__"}
    )
    errors = _validate(p)
    assert any("brief-family only" in e for e in errors)


def test_workflow_may_bind_db_agent_ids():
    """Phase 9 U1: the agents namespace extends to DB-resolved ids — a workflow
    binding an EXISTING DB agent (not proposed, not built-in) validates."""
    p = _valid_proposal()
    p["workflow_def"]["nodes"][0]["agent_ref"] = "db-resident-agent"
    assert _validate(p, ag_ids=BUILTIN_AGENT_IDS | {"db-resident-agent"}) == []
    # ...but an agent nobody can resolve is still rejected.
    assert any("unregistered agent_ref" in e for e in _validate(p))
