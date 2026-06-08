import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { Manifest, WorkflowDef } from "../api/types";
import { WorkflowBuilder } from "./WorkflowBuilder";

vi.mock("../api/endpoints");
import { getWorkflowDef, updateWorkflowDef } from "../api/endpoints";
vi.mock("../api/useManifest");
import { useManifest } from "../api/useManifest";

const MANIFEST: Manifest = {
  node_kinds: {
    step: { requires: ["handler_ref"], optional: ["agent_ref", "config_key"], edge: "next|branch" },
    human_review: { requires: ["handler_ref"], edge: "next|branch" },
    fan_out: { requires: ["over", "element_key", "into", "body"], edge: "next" },
    gather: { requires: ["compose_ref", "into"], edge: "next" },
  },
  node_handlers: ["digest_summarize", "digest_finalize_gate"],
  predicates: ["digest_route_after_summarize"],
  composers: [],
  agents: ["summarize"],
  prompt_builders: ["digest_summary_prompt"],
  parsers: ["parse_digest"],
  sources: ["hn_feed", "multi_rss"],
  renderers: ["digest", "brief"],
  families: [
    { id: "digest", source: "hn_feed", output: "digest", review: true, state: "digest" },
    { id: "brief", source: "multi_rss", output: "brief", review: false, state: "brief" },
  ],
  end: "__end__",
};

function customDef(): WorkflowDef {
  return {
    def_id: "mine",
    name: "Mine",
    description: null,
    builtin: false,
    created_at: null,
    updated_at: null,
    definition: {
      id: "mine",
      entry: "summarize",
      params: { max_redos: 2 },
      source_ref: "hn_feed",
      output_ref: "digest",
      nodes: [
        { id: "summarize", kind: "step", handler_ref: "digest_summarize", agent_ref: "summarize", config_key: "summarize_fn", next: "finalize_gate" },
        { id: "finalize_gate", kind: "step", handler_ref: "digest_finalize_gate", next: "__end__" },
      ],
    },
  };
}

function renderBuilder() {
  return render(
    <MemoryRouter initialEntries={["/workflows/mine/edit"]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/workflows/:defId/edit" element={<WorkflowBuilder />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("WorkflowBuilder", () => {
  beforeEach(() => {
    vi.mocked(useManifest).mockReturnValue(MANIFEST);
    vi.mocked(getWorkflowDef).mockResolvedValue(customDef());
    vi.mocked(updateWorkflowDef).mockReset();
  });
  afterEach(() => vi.clearAllMocks());

  it("loads a def and saves the edited definition", async () => {
    const user = userEvent.setup();
    vi.mocked(updateWorkflowDef).mockResolvedValue(customDef());
    renderBuilder();
    expect(await screen.findByText("Edit workflow: mine")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(updateWorkflowDef).toHaveBeenCalled());
    const [id, body] = vi.mocked(updateWorkflowDef).mock.calls[0];
    expect(id).toBe("mine");
    expect(body.definition).toMatchObject({ id: "mine", entry: "summarize" });
    // params round-trip through the structured editor (number stays a number)
    expect((body.definition as { params: Record<string, unknown> }).params.max_redos).toBe(2);
  });

  it("surfaces a server validation error (400)", async () => {
    const user = userEvent.setup();
    vi.mocked(updateWorkflowDef).mockRejectedValue(new ApiError(400, "node 'x': unregistered handler_ref"));
    renderBuilder();
    await screen.findByText("Edit workflow: mine");
    await user.click(screen.getByRole("button", { name: /^save$/i }));
    expect(await screen.findByText(/unregistered handler_ref/)).toBeInTheDocument();
  });
});
