"""fan_out / gather execution (Phase 7, Unit 3) — the brief's control primitives.

A `fan_out` node maps a `body` (sub-sequence of Nodes) over a state list, as a
DETERMINISTIC, ORDER-PRESERVING sequential map (brief decision D4 — NOT a LangGraph
`Send`, whose ordering/parallelism would break the brief's pinned call-order tests).
The whole map runs inside ONE LangGraph node, exactly like the hand-written
`brief_orchestrator._compose_node` it replaces — that byte-for-byte fidelity is the
no-regression proof.

Per element the engine builds a per-element SUB-STATE (a copy of the parent state
with the element bound under `element_key`), threads the `body` through it (steps
call their handlers; nested `fan_out`s recurse), then assembles a per-element value
via `collect_ref`. Bodies are intentionally limited to `step` + nested `fan_out`
(no conditionals/loops) — "够用即可,不做通用 DSL" (brief decision B).

State stays JSON-native (dict-state, matching the digest): handlers convert
to/from dataclasses at their boundaries and write plain dicts.

This module is worker-side glue, imported lazily by `engine` only when a workflow
actually has a fan_out/gather node (the digest never loads it).
"""

from __future__ import annotations


def make_fan_out_node(node, *, node_handlers: dict, composers: dict):
    """Build the LangGraph node function for a `fan_out` node."""
    _validate(node, node_handlers, composers)

    def _fan_out(state, config):
        return _run_fan_out(
            node, state, config, node_handlers=node_handlers, composers=composers
        )

    return _fan_out


def make_gather_node(node, *, node_handlers: dict, composers: dict):
    """Build the LangGraph node function for a `gather` node: assemble the result
    contract from the whole state via `compose_ref`, written to `into`."""
    try:
        compose = composers[node.compose_ref]
    except KeyError:
        raise ValueError(
            f"gather node {node.id!r} references unregistered composer {node.compose_ref!r}"
        ) from None

    def _gather(state, config):
        return {node.into: compose(state)}

    return _gather


def _run_fan_out(node, state, config, *, node_handlers, composers):
    items = state.get(node.over) or []
    collect = composers[node.collect_ref] if node.collect_ref else None
    collected = []
    for element in items:
        sub = dict(state)                 # per-element sub-state (inherits model etc.)
        sub[node.element_key] = element
        sub = _run_body(sub, node.body, config, node_handlers=node_handlers, composers=composers)
        collected.append(collect(sub) if collect else sub)
    return {node.into: collected}


def _run_body(sub, body, config, *, node_handlers, composers):
    for bnode in body:
        if bnode.kind == "step":
            delta = node_handlers[bnode.handler_ref](sub, config)
        elif bnode.kind == "fan_out":
            delta = _run_fan_out(
                bnode, sub, config, node_handlers=node_handlers, composers=composers
            )
        else:
            raise ValueError(
                f"fan_out body supports step/fan_out, not {bnode.kind!r} (node {bnode.id!r})"
            )
        sub = {**sub, **(delta or {})}
    return sub


def _validate(node, node_handlers: dict, composers: dict) -> None:
    """Surface bad refs at build time (like the top-level engine), recursing into the
    body so a nested fan_out's missing handler/composer fails on compile, not run."""
    if node.collect_ref and node.collect_ref not in composers:
        raise ValueError(
            f"fan_out node {node.id!r} references unregistered composer {node.collect_ref!r}"
        )
    for bnode in node.body or ():
        if bnode.kind == "step":
            if bnode.handler_ref not in node_handlers:
                raise ValueError(
                    f"node {bnode.id!r} references unregistered handler {bnode.handler_ref!r}"
                )
        elif bnode.kind == "fan_out":
            _validate(bnode, node_handlers, composers)
        else:
            raise ValueError(
                f"fan_out body supports step/fan_out, not {bnode.kind!r} (node {bnode.id!r})"
            )
