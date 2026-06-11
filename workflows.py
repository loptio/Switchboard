"""Workflow definitions as data (Phase 7, Unit 2/3) — the declarative graph.

A `WorkflowDef` describes a workflow's *control flow* as data: which nodes exist,
how they connect (unconditional `next`, conditional `branch`, bounded loop via a
back-edge, fan_out/gather), and which AgentDef + params each step uses. The generic
engine (`engine.build_graph`) compiles this into a LangGraph StateGraph. Node
*behaviour* (state<->agent binding, parsing, assembly) and *routing predicates*
stay as CODE, referenced by name through the component registry — the data/code
line from the brief (decision A / α).

PURE DATA: this module imports no langgraph and no Agent SDK, so the control-plane
(and a future Phase 8 synthesizer) can read/edit workflow definitions without
pulling worker machinery. The end-of-graph sentinel is the string ``END`` here;
the engine maps it to langgraph's real END at compile time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# End-of-graph sentinel (kept as a plain string so this module stays langgraph-free;
# engine.build_graph maps it to langgraph.graph.END).
END = "__end__"


@dataclass(frozen=True)
class Branch:
    """A conditional edge: a predicate (by name) returns a label; `routes` maps each
    label to a target node id (or the END sentinel). A bounded LOOP is just a Branch
    whose routes include a back-edge to an earlier node, with the bound enforced
    inside the predicate (brief decision D6)."""

    predicate_ref: str
    routes: dict  # label -> target node id (or END)


@dataclass(frozen=True)
class Node:
    """One node in a workflow.

    `kind` ∈ {step, human_review, fan_out, gather}. `conditional`/`loop` are not
    node kinds — they are expressed by a node's `branch` edge.

    step / human_review:
      - handler_ref: code component (state, config) -> state delta
      - agent_ref / config_key: which AgentDef this step runs, and which
        config["configurable"] slot holds its (injectable) agent fn — documentation
        + the meta-agent/synthesizer's hook; the handler reads the slot itself.
      - next / branch: the out-edge (exactly one).

    fan_out (Unit 3): map `body` (a sub-sequence of Nodes) over the state list at
      `over`, binding each element under `element_key` in a per-element sub-state,
      assembling each element via `collect_ref`, writing the list to `into`. Executed
      as a DETERMINISTIC, order-preserving sequential map (brief D4); `body` may
      contain `step`s and nested `fan_out`s (items ⊃ stances).
    gather (Unit 3): assemble the final result contract via `compose_ref`, into `into`.
    """

    id: str
    kind: str
    handler_ref: str | None = None
    agent_ref: str | None = None
    config_key: str | None = None
    next: str | None = None
    branch: Branch | None = None
    # fan_out / gather (Unit 3):
    over: str | None = None          # state key holding the input list
    element_key: str | None = None   # name each element is bound to in the sub-state
    body: tuple | None = None        # tuple[Node, ...] run per element
    collect_ref: str | None = None   # composer(sub_state) -> per-element value
    into: str | None = None          # parent-state key to write the collected list / result
    compose_ref: str | None = None   # gather: composer(state) -> result contract


@dataclass(frozen=True)
class WorkflowDef:
    """A workflow as data: a graph of `nodes` entered at `entry`, with workflow-level
    `params` (knobs that were hardcoded constants) and the intake/delivery components
    by name. `source_ref`/`output_ref` are declared + registered now; the runner
    still drives intake/delivery via its module-level names this phase (brief D3)."""

    id: str
    entry: str
    nodes: tuple  # tuple[Node, ...]
    params: dict = field(default_factory=dict)
    source_ref: str | None = None
    output_ref: str | None = None


# --- digest workflow (Unit 2): step + conditional + bounded loop + review gate ---
# Node ids are summarize/verify/accept_last/finalize_gate/human_review — the same
# names the former hand-written orchestrator graph used (a test pins them on _APP).
# max_redos lives here as data (was orchestrator.DEFAULT_MAX_REDOS); build_digest's
# default must agree (pinned by test_workflows).
DIGEST_DEF = WorkflowDef(
    id="news",
    entry="summarize",
    params={"max_redos": 2},
    source_ref="hn_feed",
    output_ref="digest",
    nodes=(
        Node(
            "summarize", "step",
            handler_ref="digest_summarize", agent_ref="summarize", config_key="summarize_fn",
            branch=Branch(
                "digest_route_after_summarize",
                {"verify": "verify", "summarize": "summarize", "accept_last": "accept_last", "give_up": END},
            ),
        ),
        Node(
            "verify", "step",
            handler_ref="digest_verify", agent_ref="verify", config_key="verify_fn",
            branch=Branch(
                "digest_route_after_verify",
                {"finalize_gate": "finalize_gate", "summarize": "summarize", "accept_last": "accept_last"},
            ),
        ),
        Node("accept_last", "step", handler_ref="digest_accept_last", next="finalize_gate"),
        Node(
            "finalize_gate", "step",
            handler_ref="digest_finalize_gate",
            branch=Branch("digest_route_after_finalize_gate", {"human_review": "human_review", "end": END}),
        ),
        Node(
            "human_review", "human_review",
            handler_ref="digest_human_review",
            branch=Branch("digest_route_after_human_review", {"end": END, "summarize": "summarize"}),
        ),
    ),
)


# --- brief workflow (Unit 3): step + nested fan_out + gather --------------------
# filter (step) -> compose (fan_out over kept items; per item: a summary step + a
# nested fan_out over stances producing perspectives; assembled into a BriefItem)
# -> assemble (gather: wrap the items into the Brief contract).
# stances/keep_cap are data here (were brief_agent.STANCES/KEEP_CAP); build_brief's
# defaults must agree (pinned by test_brief_workflow). Literals, not imports, keep
# this module SDK-free (brief_agent pulls the llm seam).
BRIEF_DEF = WorkflowDef(
    id="brief",
    entry="filter",
    params={"stances": ["商业", "政策", "技术"], "keep_cap": 8},
    source_ref="multi_rss",
    output_ref="brief",
    nodes=(
        Node(
            "filter", "step",
            handler_ref="brief_filter", agent_ref="filter", config_key="filter_fn",
            next="compose",
        ),
        Node(
            "compose", "fan_out",
            over="kept", element_key="item", collect_ref="brief_item", into="brief_items",
            body=(
                Node(
                    "summary", "step",
                    handler_ref="brief_summary", agent_ref="summarize_item",
                    config_key="summarize_fn",
                ),
                Node(
                    "perspectives", "fan_out",
                    over="stances", element_key="stance",
                    collect_ref="perspective_value", into="perspectives",
                    body=(
                        Node(
                            "perspective", "step",
                            handler_ref="brief_perspective", agent_ref="perspective",
                            config_key="perspective_fn",
                        ),
                    ),
                ),
            ),
            next="assemble",
        ),
        Node("assemble", "gather", compose_ref="assemble_brief", into="result", next=END),
    ),
)


# --- coding workflow (Phase 10a): one bounded coding-agent node ----------------
# A NEW family (blueprint decision 14: new shape = new code) — the first workflow to
# cross the tools=[] boundary. `coding_agent` is a new node-kind whose handler calls
# the coding seam (coding_agent.run_coding_agent) to run a bounded, workspace-confined
# agent loop; the generic engine compiles it as just another handler-running node.
# Bounds (max_turns / tool-calls / budget) live here as data (coding_orchestrator's
# build_coding defaults must agree). intake = a workspace dir (runner-driven from
# Config, not a source_ref); output_ref="coding" selects the coding harness. U1 is
# linear (coding → finalize_gate → END); U2 adds a human_review diff gate off
# finalize_gate. NOT registered in the Phase 8 manifest (the coding family is code,
# not a web-synthesizable def) — so it never enters the web palette/validator.
CODING_DEF = WorkflowDef(
    id="coding",
    entry="coding",
    params={
        "max_turns": 12, "max_tool_calls": 40, "max_budget_usd": 1.0,
        # Phase 10c: the automatic coder↔reviewer dialogue. OPT-IN (default off via
        # Config) → when off, coding routes straight to finalize_gate, byte-for-byte
        # the pre-10c path. `max_review_rounds` bounds the dialogue (like max_redos).
        "max_review_rounds": 2,
    },
    source_ref=None,
    output_ref="coding",
    nodes=(
        # Phase 10c: coding branches to the automatic reviewer when auto-review is ON
        # and there is reviewable work; otherwise straight to finalize_gate (the
        # pre-10c default — byte-for-byte the U1/U2 path).
        Node(
            "coding", "coding_agent",
            handler_ref="coding_run", config_key="coding_fn",
            branch=Branch("coding_route_after_coding", {"review": "review", "finalize_gate": "finalize_gate"}),
        ),
        # review (Phase 10c): the automatic reviewer reads the diff → approve → finalize_gate;
        # needs-work with rounds left → back to coding (with the reviewer's feedback);
        # rounds exhausted → finalize_gate (not converged — the human gate still decides).
        Node(
            "review", "step",
            handler_ref="coding_review", config_key="reviewer_fn",
            branch=Branch("coding_route_after_review", {"coding": "coding", "finalize_gate": "finalize_gate"}),
        ),
        # finalize_gate routes to the human diff-review gate when review is ON (U2),
        # else straight to END (the non-review default — byte-for-byte the U1 path).
        Node(
            "finalize_gate", "step",
            handler_ref="coding_finalize_gate",
            branch=Branch("coding_route_after_finalize_gate", {"human_review": "human_review", "end": END}),
        ),
        # human_review: approve → END; redo (with feedback) → a fresh bounded coding loop.
        Node(
            "human_review", "human_review",
            handler_ref="coding_human_review",
            branch=Branch("coding_route_after_human_review", {"end": END, "coding": "coding"}),
        ),
    ),
)


# --- meta workflow (Phase 9): draft → validate → human review ------------------
# The meta-agent family: a per-run REQUEST (riding the runs.coding_task column, the
# Phase 10b-1 per-run-task pipe) is drafted into a WorkflowDef/AgentDef PROPOSAL by
# an llm-seam agent, checked by a DETERMINISTIC validate node (defs_validate + the
# meta-only rules in meta_agent.validate_proposal), redrafted on errors (bounded by
# max_redos), and presented at a human_review gate. Persistence happens ONLY after
# approval, in runner._finalize_meta — never inside the graph (interrupt() replays
# its node on resume, so the gate handler must stay side-effect-free). Like coding,
# this family is CODE: its handlers live in meta_orchestrator's LOCAL registries and
# never enter the Phase 8 manifest — so a proposal can't draft meta (or coding)
# workflows, and the synthesizer UI can't edit this def. Runner-side, a meta run
# WITHOUT the review flag is refused (the Phase 9 human-approval guardrail).
META_DEF = WorkflowDef(
    id="meta",
    entry="draft",
    params={"max_redos": 2},
    source_ref=None,
    output_ref="meta",
    nodes=(
        Node(
            "draft", "step",
            handler_ref="meta_draft", config_key="draft_fn",
            next="validate",
        ),
        Node(
            "validate", "step",
            handler_ref="meta_validate",
            branch=Branch(
                "meta_route_after_validate",
                {"human_review": "human_review", "draft": "draft", "give_up": END},
            ),
        ),
        Node(
            "human_review", "human_review",
            handler_ref="meta_human_review",
            branch=Branch("meta_route_after_human_review", {"end": END, "draft": "draft"}),
        ),
    ),
)


# Looked up by id (brief §6 footnote: runner finds the def by run.workflow). "news"
# is the legacy digest label; "digest" is its alias; "coding" is the Phase 10a
# family; "meta" is the Phase 9 meta-agent family.
WORKFLOWS: dict[str, WorkflowDef] = {
    "news": DIGEST_DEF,
    "digest": DIGEST_DEF,
    "brief": BRIEF_DEF,
    "coding": CODING_DEF,
    "meta": META_DEF,
}


# --- (de)serialization: WorkflowDef <-> JSON (Phase 8) ----------------------
# Pure data (no langgraph/SDK): the control-plane synthesizer reads/writes these
# JSON dicts; the worker deserializes one to run it. `to_dict` prunes None optional
# fields for readable JSON; `from_dict` tolerates their absence (.get) and rebuilds
# the frozen dataclasses with TUPLES for `nodes`/`body`, so round-trip identity
# holds (from_dict(to_dict(x)) == x) — pinned by tests. The END sentinel stays the
# plain string "__end__" throughout (JSON-clean).

_NODE_OPTIONAL_STR_FIELDS = (
    "handler_ref", "agent_ref", "config_key", "next",
    "over", "element_key", "collect_ref", "into", "compose_ref",
)


def _node_to_dict(node: Node) -> dict:
    d: dict = {"id": node.id, "kind": node.kind}
    for field_name in _NODE_OPTIONAL_STR_FIELDS:
        value = getattr(node, field_name)
        if value is not None:
            d[field_name] = value
    if node.branch is not None:
        d["branch"] = {
            "predicate_ref": node.branch.predicate_ref,
            "routes": dict(node.branch.routes),
        }
    if node.body is not None:
        d["body"] = [_node_to_dict(b) for b in node.body]
    return d


def _node_from_dict(d: dict) -> Node:
    branch = d.get("branch")
    body = d.get("body")
    return Node(
        id=d["id"],
        kind=d["kind"],
        handler_ref=d.get("handler_ref"),
        agent_ref=d.get("agent_ref"),
        config_key=d.get("config_key"),
        next=d.get("next"),
        branch=(
            Branch(predicate_ref=branch["predicate_ref"], routes=dict(branch["routes"]))
            if branch
            else None
        ),
        over=d.get("over"),
        element_key=d.get("element_key"),
        body=tuple(_node_from_dict(b) for b in body) if body is not None else None,
        collect_ref=d.get("collect_ref"),
        into=d.get("into"),
        compose_ref=d.get("compose_ref"),
    )


def workflow_def_to_dict(wf: WorkflowDef) -> dict:
    """Serialize a WorkflowDef to a plain JSON-able dict. Inverse of from_dict."""
    d: dict = {
        "id": wf.id,
        "entry": wf.entry,
        "params": dict(wf.params),
        "nodes": [_node_to_dict(n) for n in wf.nodes],
    }
    if wf.source_ref is not None:
        d["source_ref"] = wf.source_ref
    if wf.output_ref is not None:
        d["output_ref"] = wf.output_ref
    return d


def workflow_def_from_dict(d: dict) -> WorkflowDef:
    """Rebuild a WorkflowDef from its dict form (lists -> tuples so frozen-dataclass
    equality round-trips). Inverse of workflow_def_to_dict."""
    return WorkflowDef(
        id=d["id"],
        entry=d["entry"],
        nodes=tuple(_node_from_dict(n) for n in d.get("nodes", [])),
        params=dict(d.get("params", {})),
        source_ref=d.get("source_ref"),
        output_ref=d.get("output_ref"),
    )


def iter_agent_bindings(wf: WorkflowDef):
    """Yield (agent_ref, config_key) for every node in `wf` that binds an agent,
    recursing into fan_out bodies. The runner uses this to resolve + assemble a
    workflow's agents once per run. Pure data — no SDK/langgraph."""

    def _walk(nodes):
        for n in nodes:
            if n.agent_ref is not None:
                yield (n.agent_ref, n.config_key)
            if n.body is not None:
                yield from _walk(n.body)

    yield from _walk(wf.nodes)
