"""Offline tests for the workflow definitions (Phase 7, Unit 2/3).

Pins that DIGEST_DEF is well-formed (every node has exactly one out-edge, every
edge target is a real node id or END, every handler/predicate ref resolves in the
registry) and that the dogfooded digest graph carries the node names the engine
must produce. The brief def is added/tested in Unit 3.
"""

import brief_agent
import brief_orchestrator  # noqa: F401 — side effect: registers brief glue
import components
import orchestrator  # noqa: F401 — imported for its side effect: registers digest glue
from workflows import BRIEF_DEF, DIGEST_DEF, END, WORKFLOWS, WorkflowDef


def _node_ids(wf: WorkflowDef) -> set[str]:
    return {n.id for n in wf.nodes}


def test_digest_def_identity_and_lookup():
    assert DIGEST_DEF.id == "news"
    assert DIGEST_DEF.entry == "summarize"
    assert WORKFLOWS["news"] is DIGEST_DEF
    assert WORKFLOWS["digest"] is DIGEST_DEF  # legacy alias resolves to the same def


def test_digest_max_redos_param_agrees_with_build_digest_default():
    # max_redos is data on the def; build_digest's default must agree (no drift).
    assert DIGEST_DEF.params["max_redos"] == orchestrator.DEFAULT_MAX_REDOS


def test_digest_nodes_are_the_expected_five():
    assert _node_ids(DIGEST_DEF) == {
        "summarize", "verify", "accept_last", "finalize_gate", "human_review",
    }


def test_every_node_has_exactly_one_out_edge():
    for n in DIGEST_DEF.nodes:
        assert (n.next is not None) ^ (n.branch is not None), n.id


def test_every_edge_target_is_a_real_node_or_end():
    ids = _node_ids(DIGEST_DEF)
    for n in DIGEST_DEF.nodes:
        if n.next is not None:
            assert n.next == END or n.next in ids, n.id
        else:
            for label, target in n.branch.routes.items():
                assert target == END or target in ids, (n.id, label, target)


def test_handler_and_predicate_refs_resolve_in_registry():
    # importing orchestrator registered the digest glue by name.
    for n in DIGEST_DEF.nodes:
        assert n.handler_ref in components.NODE_HANDLERS, n.id
        if n.branch is not None:
            assert n.branch.predicate_ref in components.PREDICATES, n.id


def test_compiled_digest_app_has_all_five_nodes():
    # the data -> graph mapping is complete: every WorkflowDef node became a graph node.
    nodes = set(orchestrator._APP.get_graph().nodes)
    assert _node_ids(DIGEST_DEF) <= nodes


# --- brief workflow (Unit 3) -----------------------------------------------


def test_brief_def_identity_and_lookup():
    assert BRIEF_DEF.id == "brief"
    assert BRIEF_DEF.entry == "filter"
    assert WORKFLOWS["brief"] is BRIEF_DEF


def test_brief_params_agree_with_brief_agent_constants():
    # stances/keep_cap are data on the def; build_brief's defaults (brief_agent
    # constants) must agree (no drift).
    assert BRIEF_DEF.params["stances"] == list(brief_agent.STANCES)
    assert BRIEF_DEF.params["keep_cap"] == brief_agent.KEEP_CAP


def test_brief_top_level_pipeline_is_filter_compose_assemble():
    top = [(n.id, n.kind) for n in BRIEF_DEF.nodes]
    assert top == [("filter", "step"), ("compose", "fan_out"), ("assemble", "gather")]
    by_id = {n.id: n for n in BRIEF_DEF.nodes}
    assert by_id["filter"].next == "compose"
    assert by_id["compose"].next == "assemble"
    assert by_id["assemble"].next == END


def test_brief_compose_is_a_nested_fan_out():
    compose = {n.id: n for n in BRIEF_DEF.nodes}["compose"]
    assert compose.over == "kept" and compose.into == "brief_items"
    body_kinds = [(n.id, n.kind) for n in compose.body]
    assert body_kinds == [("summary", "step"), ("perspectives", "fan_out")]
    inner = compose.body[1]
    assert inner.over == "stances" and inner.into == "perspectives"
    assert [(n.id, n.kind) for n in inner.body] == [("perspective", "step")]


def test_brief_handler_and_composer_refs_resolve_in_registry():
    # importing brief_orchestrator registered the brief glue by name.
    def _check(node):
        if node.kind == "step":
            assert node.handler_ref in components.NODE_HANDLERS, node.id
        if node.collect_ref:
            assert node.collect_ref in components.COMPOSERS, node.id
        for child in node.body or ():
            _check(child)

    for n in BRIEF_DEF.nodes:
        _check(n)
    assert "assemble_brief" in components.COMPOSERS  # the gather composer


def test_compiled_brief_app_has_top_level_nodes():
    nodes = set(brief_orchestrator._APP.get_graph().nodes)
    assert {"filter", "compose", "assemble"} <= nodes
