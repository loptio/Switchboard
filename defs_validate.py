"""Validation for workflow & agent defs (Phase 8) — PURE DATA, web-safe.

Two guards (brief decision C):
- Guard #1 (save): the synthesizer API runs `validate_workflow_def` before
  persisting, so a broken def is rejected the moment it is saved — never left to
  crash the worker on a scheduled midnight run.
- Guard #2 (load): the worker runs the SAME function before compiling a
  DB-resolved def, and then `engine.build_graph(...).compile()` independently
  raises on bad refs / topology.

The checks mirror what `engine.build_graph` + `engine_fanout._validate` enforce, so
"passes save" ⟹ "compiles at load"; they ADD the family checks (source_ref /
output_ref) the engine never sees. Operates on the def DICT + the pure-data
manifest (names only) — no SDK/langgraph import, so the web tier can validate too.

Returns a list of human-readable error strings (empty = valid); the API turns a
non-empty list into a 400.
"""

from __future__ import annotations

_KINDS_WITH_HANDLER = ("step", "human_review")
_ALL_KINDS = ("step", "human_review", "fan_out", "gather")


def validate_workflow_def(d: dict, manifest: dict) -> list[str]:
    """Validate a workflow def dict against the component manifest."""
    errors: list[str] = []
    end = manifest.get("end", "__end__")
    handlers = set(manifest.get("node_handlers", []))
    predicates = set(manifest.get("predicates", []))
    composers = set(manifest.get("composers", []))
    agents = set(manifest.get("agents", []))
    sources = set(manifest.get("sources", []))
    renderers = set(manifest.get("renderers", []))
    families = {f["output"]: f for f in manifest.get("families", [])}

    if not d.get("id"):
        errors.append("workflow def missing 'id'")

    entry = d.get("entry")
    nodes = d.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        errors.append("workflow def 'nodes' must be a non-empty list")
        return errors  # nothing more to check without nodes

    raw_ids = [n.get("id") for n in nodes if isinstance(n, dict)]
    node_ids = set(raw_ids)
    if len(raw_ids) != len(node_ids):
        errors.append("duplicate node ids")
    if not entry:
        errors.append("workflow def missing 'entry'")
    elif entry not in node_ids:
        errors.append(f"entry {entry!r} is not a node id")

    def _check_node(n: dict, *, in_body: bool) -> None:
        nid = n.get("id", "?")
        kind = n.get("kind")
        if kind not in _ALL_KINDS:
            errors.append(f"node {nid!r}: unknown kind {kind!r}")
            return
        if in_body and kind not in ("step", "fan_out"):
            errors.append(
                f"fan_out body node {nid!r}: only step/fan_out allowed, not {kind!r}"
            )

        branch = n.get("branch")
        nxt = n.get("next")

        if kind in _KINDS_WITH_HANDLER:
            if n.get("handler_ref") not in handlers:
                errors.append(
                    f"node {nid!r}: unregistered handler_ref {n.get('handler_ref')!r}"
                )
            aref = n.get("agent_ref")
            if aref is not None and aref not in agents:
                errors.append(f"node {nid!r}: unregistered agent_ref {aref!r}")

        # Edges exist only at the TOP level: nodes inside a fan_out body run as a
        # sequential map (engine_fanout._run_body), so they carry no next/branch.
        if not in_body:
            if kind in _KINDS_WITH_HANDLER and (nxt is not None) == (branch is not None):
                errors.append(f"node {nid!r}: needs exactly one of 'next' or 'branch'")
            if isinstance(branch, dict):
                if branch.get("predicate_ref") not in predicates:
                    errors.append(
                        f"node {nid!r}: unregistered predicate_ref "
                        f"{branch.get('predicate_ref')!r}"
                    )
                for label, tgt in (branch.get("routes") or {}).items():
                    if tgt != end and tgt not in node_ids:
                        errors.append(
                            f"node {nid!r}: branch route {label!r} -> unknown target {tgt!r}"
                        )
            elif branch is not None:
                errors.append(f"node {nid!r}: 'branch' must be an object")
            if nxt is not None and nxt != end and nxt not in node_ids:
                errors.append(f"node {nid!r}: 'next' -> unknown target {nxt!r}")

        if kind == "fan_out":
            for req in ("over", "element_key", "into"):
                if not n.get(req):
                    errors.append(f"fan_out node {nid!r}: missing {req!r}")
            cref = n.get("collect_ref")
            if cref is not None and cref not in composers:
                errors.append(f"fan_out node {nid!r}: unregistered collect_ref {cref!r}")
            body = n.get("body")
            if not isinstance(body, list) or not body:
                errors.append(f"fan_out node {nid!r}: 'body' must be a non-empty list")
            else:
                for child in body:
                    _check_node(child, in_body=True)
            if not in_body and nxt is None:
                errors.append(f"fan_out node {nid!r}: needs 'next'")
        if kind == "gather":
            if n.get("compose_ref") not in composers:
                errors.append(
                    f"gather node {nid!r}: unregistered compose_ref {n.get('compose_ref')!r}"
                )
            if not n.get("into"):
                errors.append(f"gather node {nid!r}: missing 'into'")
            if nxt is None:
                errors.append(f"gather node {nid!r}: needs 'next'")

    for n in nodes:
        if isinstance(n, dict):
            _check_node(n, in_body=False)
        else:
            errors.append(f"node {n!r} is not an object")

    # Reachability: END must be reachable from entry over top-level edges, else the
    # run can never terminate (engine.build_graph won't catch this — save-time only).
    if entry in node_ids and not _end_reachable(nodes, entry, end):
        errors.append(f"END {end!r} is not reachable from entry {entry!r}")

    # Family: output_ref selects the runner harness; source_ref must match it.
    src = d.get("source_ref")
    out = d.get("output_ref")
    if not out:
        errors.append("workflow def missing 'output_ref' (selects the runner harness)")
    elif out not in renderers:
        errors.append(f"unregistered output_ref {out!r}")
    elif out in families:
        if src is not None and src != families[out]["source"]:
            errors.append(
                f"source_ref {src!r} does not match the {out!r} family source "
                f"{families[out]['source']!r}"
            )
    if src is not None and src not in sources:
        errors.append(f"unregistered source_ref {src!r}")

    return errors


def _end_reachable(nodes: list, entry: str, end: str) -> bool:
    by_id = {n.get("id"): n for n in nodes if isinstance(n, dict)}
    seen: set = set()
    stack = [entry]
    while stack:
        cur = stack.pop()
        if cur == end:
            return True
        if cur in seen or cur not in by_id:
            continue
        seen.add(cur)
        n = by_id[cur]
        if n.get("next") is not None:
            stack.append(n["next"])
        branch = n.get("branch")
        if isinstance(branch, dict):
            stack.extend((branch.get("routes") or {}).values())
    return False


def validate_agent_def(d: dict, manifest: dict) -> list[str]:
    """Validate an agent def dict against the component manifest."""
    errors: list[str] = []
    builders = set(manifest.get("prompt_builders", []))
    parsers = set(manifest.get("parsers", []))

    if not d.get("id"):
        errors.append("agent def missing 'id'")
    sp = d.get("system_prompt")
    if not isinstance(sp, str) or not sp.strip():
        errors.append("agent def 'system_prompt' must be a non-empty string")
    if d.get("prompt_builder_ref") not in builders:
        errors.append(
            f"unregistered prompt_builder_ref {d.get('prompt_builder_ref')!r}"
        )
    if d.get("parser_ref") not in parsers:
        errors.append(f"unregistered parser_ref {d.get('parser_ref')!r}")
    model = d.get("model")
    if model is not None and not isinstance(model, str):
        errors.append("agent def 'model' must be a string or null")
    if not isinstance(d.get("params", {}), dict):
        errors.append("agent def 'params' must be an object")
    return errors
