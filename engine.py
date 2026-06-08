"""Generic orchestrator (Phase 7) — compile a WorkflowDef into a LangGraph graph.

`build_graph` reads a declarative WorkflowDef (data) and wires a LangGraph
StateGraph from it:

- `step` / `human_review` nodes  -> g.add_node(id, handler)         (handler = code by name)
- `next` edge                    -> g.add_edge(id, target)
- `branch` edge (conditional /   -> g.add_conditional_edges(id, predicate, {label: target})
   bounded loop via a back-edge)
- `fan_out` / `gather` (Unit 3)  -> a deterministic, order-preserving sequential map
                                    + a result-assembly node

The engine holds NO workflow-specific logic: node behaviour (state<->agent binding,
parsing, assembly) and routing predicates are CODE passed in by name (the component
registry). Nodes reach the model only via the injected agents (the llm.py seam);
the engine never imports the Agent SDK — only langgraph.

This is the "behaviour = hand-written graph" engine: compiling the digest/brief
WorkflowDefs reproduces the exact graphs they replace, so the existing digest/brief
tests are the no-regression proof.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from workflows import END as _END_SENTINEL
from workflows import Node, WorkflowDef


def _target(name: str):
    """Map a WorkflowDef edge target to a langgraph node (or the END sentinel)."""
    return END if name == _END_SENTINEL else name


def build_graph(
    wf: WorkflowDef,
    state_schema,
    *,
    node_handlers: dict,
    predicates: dict,
    composers: dict | None = None,
) -> StateGraph:
    """Compile `wf` into an (uncompiled) LangGraph StateGraph over `state_schema`.

    `node_handlers` / `predicates` / `composers` are the code components, by name
    (the registry). Returns the StateGraph builder so the caller can `.compile()`
    it with or without a checkpointer (the digest does both). All nodes are added
    before any edges are wired (conditional-edge targets must already exist).
    """
    composers = composers or {}
    g = StateGraph(state_schema)
    _add_nodes(g, wf.nodes, node_handlers=node_handlers, composers=composers)
    g.add_edge(START, wf.entry)
    _wire_edges(g, wf.nodes, predicates=predicates)
    return g


def _add_nodes(g: StateGraph, nodes, *, node_handlers: dict, composers: dict) -> None:
    for node in nodes:
        if node.kind in ("step", "human_review"):
            try:
                handler = node_handlers[node.handler_ref]
            except KeyError:
                raise ValueError(
                    f"node {node.id!r} references unregistered handler {node.handler_ref!r}"
                ) from None
            g.add_node(node.id, handler)
        elif node.kind in ("fan_out", "gather"):
            # Implemented in Unit 3 (brief). Imported lazily so Unit 2 (digest) needs
            # nothing of it.
            from engine_fanout import make_fan_out_node, make_gather_node  # noqa: PLC0415

            maker = make_fan_out_node if node.kind == "fan_out" else make_gather_node
            g.add_node(node.id, maker(node, node_handlers=node_handlers, composers=composers))
        else:
            raise ValueError(f"node {node.id!r} has unknown kind {node.kind!r}")


def _wire_edges(g: StateGraph, nodes, *, predicates: dict) -> None:
    for node in nodes:
        if node.branch is not None:
            try:
                predicate = predicates[node.branch.predicate_ref]
            except KeyError:
                raise ValueError(
                    f"node {node.id!r} references unregistered predicate "
                    f"{node.branch.predicate_ref!r}"
                ) from None
            mapping = {label: _target(t) for label, t in node.branch.routes.items()}
            g.add_conditional_edges(node.id, predicate, mapping)
        elif node.next is not None:
            g.add_edge(node.id, _target(node.next))
        else:
            raise ValueError(f"node {node.id!r} has neither `next` nor `branch`")
