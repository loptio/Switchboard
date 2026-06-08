"""WorkflowDef / AgentDef <-> JSON round-trip (Phase 8, U1).

Round-trip identity (from_dict(to_dict(x)) == x) is the JSON contract: it must hold
for the two built-ins (incl. the brief's nested fan_out) so a def can be persisted
and re-run faithfully. Also pins the END sentinel survives as the plain string and
that the dict form is pure JSON (no tuples / dataclasses leak).
"""

import json

import agentdefs as A
import workflows as W


def test_workflow_def_roundtrip_digest():
    assert W.workflow_def_from_dict(W.workflow_def_to_dict(W.DIGEST_DEF)) == W.DIGEST_DEF


def test_workflow_def_roundtrip_brief_nested_fan_out():
    # the brief exercises fan_out + a NESTED fan_out + gather + a body of step nodes.
    assert W.workflow_def_from_dict(W.workflow_def_to_dict(W.BRIEF_DEF)) == W.BRIEF_DEF


def test_workflow_def_to_dict_is_pure_json():
    d = W.workflow_def_to_dict(W.BRIEF_DEF)
    assert json.loads(json.dumps(d, ensure_ascii=False)) == d  # no tuples/dataclasses


def test_end_sentinel_survives_as_plain_string():
    d = W.workflow_def_to_dict(W.DIGEST_DEF)
    summarize = next(n for n in d["nodes"] if n["id"] == "summarize")
    assert summarize["branch"]["routes"]["give_up"] == "__end__"


def test_agent_def_roundtrip_all_builtins():
    for ad in A.AGENT_DEFS.values():
        assert A.agent_def_from_dict(A.agent_def_to_dict(ad)) == ad


def test_agent_def_none_model_and_empty_params_roundtrip():
    d = A.agent_def_to_dict(A.AGENT_DEFS["verify"])  # model=None, params={}
    assert d["model"] is None and d["params"] == {}
    assert A.agent_def_from_dict(d) == A.AGENT_DEFS["verify"]


def test_iter_agent_bindings_digest():
    assert dict(W.iter_agent_bindings(W.DIGEST_DEF)) == {
        "summarize": "summarize_fn",
        "verify": "verify_fn",
    }


def test_iter_agent_bindings_brief_recurses_into_fan_out():
    assert dict(W.iter_agent_bindings(W.BRIEF_DEF)) == {
        "filter": "filter_fn",
        "summarize_item": "summarize_fn",
        "perspective": "perspective_fn",
    }
