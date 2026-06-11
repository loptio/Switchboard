import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Run, WorkflowDef } from "../api/types";
import { WorkflowsPage } from "./WorkflowsPage";

vi.mock("../api/endpoints");
import {
  cloneWorkflowDef,
  deleteWorkflowDef,
  listWorkflowDefs,
  triggerRun,
} from "../api/endpoints";

function wf(over: Partial<WorkflowDef> = {}): WorkflowDef {
  return {
    def_id: "news",
    name: "news",
    description: null,
    builtin: true,
    created_at: null,
    updated_at: null,
    definition: {
      id: "news",
      entry: "summarize",
      params: {},
      source_ref: "hn_feed",
      output_ref: "digest",
      nodes: [{ id: "summarize", kind: "step" }],
    },
    ...over,
  };
}

function renderPage() {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <WorkflowsPage />
    </MemoryRouter>,
  );
}

describe("WorkflowsPage", () => {
  beforeEach(() => {
    vi.mocked(listWorkflowDefs).mockResolvedValue([]);
    vi.mocked(triggerRun).mockReset();
    vi.mocked(cloneWorkflowDef).mockReset();
    vi.mocked(deleteWorkflowDef).mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
    vi.unstubAllGlobals();
  });

  it("lists built-in and custom workflows in the sidebar", async () => {
    vi.mocked(listWorkflowDefs).mockResolvedValue([
      wf(),
      wf({ def_id: "mine", name: "Mine", builtin: false }),
    ]);
    renderPage();
    const list = await screen.findByRole("navigation", { name: /workflows/i });
    expect(within(list).getByText("Mine")).toBeInTheDocument();
    expect(within(list).getByText("built-in")).toBeInTheDocument();
    expect(within(list).getByText("custom")).toBeInTheDocument();
    // the first workflow auto-selects → its graph renders on the right
    expect(await screen.findByRole("img", { name: /workflow graph/i })).toBeInTheDocument();
  });

  it("shows the selected workflow's graph and switches on click", async () => {
    const user = userEvent.setup();
    vi.mocked(listWorkflowDefs).mockResolvedValue([
      wf(),
      wf({ def_id: "mine", name: "Mine", builtin: false }),
    ]);
    renderPage();
    const list = await screen.findByRole("navigation", { name: /workflows/i });
    await user.click(within(list).getByText("Mine"));
    // the detail title reflects the newly selected workflow
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Mine" })).toBeInTheDocument(),
    );
  });

  it("runs a workflow via the handoff", async () => {
    const user = userEvent.setup();
    vi.mocked(listWorkflowDefs).mockResolvedValue([wf({ def_id: "mine", name: "Mine", builtin: false })]);
    vi.mocked(triggerRun).mockResolvedValue({ id: "r1" } as Run);
    renderPage();
    await user.click(await screen.findByRole("button", { name: /run now/i }));
    await waitFor(() => expect(triggerRun).toHaveBeenCalledWith("mine", false));
  });

  it("offers a review run for the coding family (Phase 10a)", async () => {
    const user = userEvent.setup();
    vi.mocked(listWorkflowDefs).mockResolvedValue([
      wf({
        def_id: "coding",
        name: "coding",
        definition: {
          id: "coding",
          entry: "coding",
          params: {},
          output_ref: "coding",
          nodes: [{ id: "coding", kind: "step" }],
        },
      }),
    ]);
    vi.mocked(triggerRun).mockResolvedValue({ id: "c1" } as Run);
    renderPage();
    await user.click(await screen.findByRole("button", { name: /run \(review\)/i }));
    await waitFor(() => expect(triggerRun).toHaveBeenCalledWith("coding", true));
  });

  it("clones a built-in (prompting for a new id)", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("prompt", () => "my-news");
    vi.mocked(listWorkflowDefs).mockResolvedValue([wf()]);
    vi.mocked(cloneWorkflowDef).mockResolvedValue(wf({ def_id: "my-news", builtin: false }));
    renderPage();
    await user.click(await screen.findByRole("button", { name: /clone/i }));
    await waitFor(() => expect(cloneWorkflowDef).toHaveBeenCalledWith("news", "my-news"));
  });
});
