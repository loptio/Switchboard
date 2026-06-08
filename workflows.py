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


# Looked up by id (brief §6 footnote: runner finds the def by run.workflow). "news"
# is the legacy digest label; "digest" is its alias.
WORKFLOWS: dict[str, WorkflowDef] = {
    "news": DIGEST_DEF,
    "digest": DIGEST_DEF,
    "brief": BRIEF_DEF,
}
