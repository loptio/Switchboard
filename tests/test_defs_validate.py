"""Two-guard validation alignment (Phase 8, U1) — pins "passes save ⟹ loads".

The save-time validator (defs_validate, pure data) and the load-time guard
(engine.build_graph(...).compile()) must agree:
- VALID defs (the built-ins + hand-made) pass BOTH.
- STRUCTURAL-bad defs (bad handler_ref / dangling edge / unknown kind / no edge)
  are rejected by BOTH.
- SAVE-ONLY-bad defs (unreachable END / bad output_ref / family mismatch) are
  caught by defs_validate; the engine never sees these (output_ref/source_ref are
  not its concern) — which is exactly why save-time validation exists.
"""

import copy

import pytest

import brief_orchestrator  # noqa: F401 — registers brief glue + provides _State
import orchestrator  # noqa: F401 — registers digest glue + provides _State

import agentdefs
import components
import defs_validate as V
import engine
import workflows as W
from manifest import build_manifest

M = build_manifest()


def _digest_dict() -> dict:
    return W.workflow_def_to_dict(W.DIGEST_DEF)


def _compile(def_dict: dict, state) -> None:
    """The load-time guard: compile via the generic engine + the full registries."""
    wf = W.workflow_def_from_dict(def_dict)
    engine.build_graph(
        wf,
        state,
        node_handlers=components.NODE_HANDLERS,
        predicates=components.PREDICATES,
        composers=components.COMPOSERS,
    ).compile()


# --- valid: BOTH accept ----------------------------------------------------

def test_builtins_pass_both_guards():
    assert V.validate_workflow_def(_digest_dict(), M) == []
    _compile(_digest_dict(), orchestrator._State)  # must not raise
    assert V.validate_workflow_def(W.workflow_def_to_dict(W.BRIEF_DEF), M) == []
    _compile(W.workflow_def_to_dict(W.BRIEF_DEF), brief_orchestrator._State)


# --- structural-bad: BOTH reject -------------------------------------------

def test_bad_handler_ref_rejected_by_both():
    d = _digest_dict()
    d["nodes"][0]["handler_ref"] = "ghost"
    assert V.validate_workflow_def(d, M)
    with pytest.raises(Exception):
        _compile(d, orchestrator._State)


def test_dangling_edge_rejected_by_both():
    d = _digest_dict()
    d["nodes"][0]["branch"]["routes"]["verify"] = "ghost"
    assert V.validate_workflow_def(d, M)
    with pytest.raises(Exception):
        _compile(d, orchestrator._State)


def test_unknown_kind_rejected_by_both():
    d = _digest_dict()
    d["nodes"][2]["kind"] = "bogus"
    assert V.validate_workflow_def(d, M)
    with pytest.raises(Exception):
        _compile(d, orchestrator._State)


def test_node_without_edge_rejected_by_both():
    d = _digest_dict()
    d["nodes"][2].pop("next", None)  # accept_last loses its only out-edge
    assert V.validate_workflow_def(d, M)
    with pytest.raises(Exception):
        _compile(d, orchestrator._State)


# --- save-only: defs_validate rejects (the engine can't see these) ---------

def test_bad_output_ref_is_save_only():
    d = _digest_dict()
    d["output_ref"] = "bogus"
    assert any("output_ref" in e for e in V.validate_workflow_def(d, M))


def test_unreachable_end_is_save_only():
    d = {
        "id": "x", "entry": "a", "params": {},
        "output_ref": "digest", "source_ref": "hn_feed",
        "nodes": [
            {"id": "a", "kind": "step", "handler_ref": "digest_accept_last", "next": "a"}
        ],
    }
    assert any("reachable" in e for e in V.validate_workflow_def(d, M))


def test_family_source_mismatch_is_save_only():
    d = _digest_dict()
    d["source_ref"] = "multi_rss"  # the digest family wants hn_feed
    assert any("match" in e for e in V.validate_workflow_def(d, M))


def test_missing_output_ref_rejected():
    d = _digest_dict()
    d.pop("output_ref")
    assert any("output_ref" in e for e in V.validate_workflow_def(d, M))


# --- agent defs ------------------------------------------------------------

def test_builtin_agent_defs_validate():
    for ad in agentdefs.AGENT_DEFS.values():
        assert V.validate_agent_def(agentdefs.agent_def_to_dict(ad), M) == []


def test_bad_agent_def_rejected():
    bad = {"id": "x", "system_prompt": "", "prompt_builder_ref": "nope", "parser_ref": "nope"}
    errs = V.validate_agent_def(bad, M)
    assert len(errs) == 3  # empty prompt + bad builder + bad parser
