import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { WorkflowDefinition } from "../api/types";
import { WorkflowGraph } from "./WorkflowGraph";

// A digest-shaped def: a loop (verify → summarize), a branch, a review gate, END.
const DIGEST: WorkflowDefinition = {
  id: "news",
  entry: "summarize",
  params: { max_redos: 2 },
  source_ref: "hn_feed",
  output_ref: "digest",
  nodes: [
    {
      id: "summarize",
      kind: "step",
      handler_ref: "digest_summarize",
      agent_ref: "summarize",
      branch: {
        predicate_ref: "digest_route_after_summarize",
        routes: { verify: "verify", summarize: "summarize", give_up: "__end__" },
      },
    },
    {
      id: "verify",
      kind: "step",
      handler_ref: "digest_verify",
      agent_ref: "verify",
      branch: {
        predicate_ref: "digest_route_after_verify",
        routes: { human_review: "human_review", summarize: "summarize" },
      },
    },
    {
      id: "human_review",
      kind: "human_review",
      handler_ref: "digest_human_review",
      branch: { predicate_ref: "p", routes: { end: "__end__" } },
    },
  ],
};

describe("WorkflowGraph", () => {
  // node ids render in <text class=nodeId>; some also appear as branch route labels
  // (a route key can equal its target node's id), so scope node-presence checks to
  // the node-id text elements.
  function nodeIds(container: HTMLElement): string[] {
    return [...container.querySelectorAll("text")]
      .filter((t) => t.getAttribute("class")?.includes("nodeId"))
      .map((t) => t.textContent || "");
  }

  it("renders every node id and the END terminal", () => {
    const { container } = render(<WorkflowGraph definition={DIGEST} />);
    expect(nodeIds(container)).toEqual(
      expect.arrayContaining(["summarize", "verify", "human_review"]),
    );
    expect(screen.getByText("END")).toBeInTheDocument();
  });

  it("labels the entry node and shows agent bindings", () => {
    render(<WorkflowGraph definition={DIGEST} />);
    expect(screen.getByText(/entry/)).toBeInTheDocument();
    expect(screen.getByText(/agent: summarize/)).toBeInTheDocument();
  });

  it("draws loop back-edge labels", () => {
    render(<WorkflowGraph definition={DIGEST} />);
    // both verify→summarize and summarize→summarize are back-edges → loop labels
    expect(screen.getAllByText(/↩ summarize/).length).toBeGreaterThanOrEqual(1);
  });

  it("overlays live per-node run state", () => {
    const { container } = render(
      <WorkflowGraph
        definition={DIGEST}
        statuses={{ summarize: "done", verify: "running", human_review: "pending" }}
      />,
    );
    // a running node shows the pulsing indicator circle
    expect(container.querySelector("circle")).toBeInTheDocument();
  });

  it("renders a fan_out node with its body inline", () => {
    const brief: WorkflowDefinition = {
      id: "brief",
      entry: "compose",
      params: {},
      output_ref: "brief",
      nodes: [
        {
          id: "compose",
          kind: "fan_out",
          over: "kept",
          element_key: "item",
          into: "items",
          body: [{ id: "summary", kind: "step", handler_ref: "brief_summary" }],
          next: "__end__",
        },
      ],
    };
    render(<WorkflowGraph definition={brief} />);
    expect(screen.getByText("compose")).toBeInTheDocument();
    expect(screen.getByText(/over kept/)).toBeInTheDocument();
  });
});
