import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { NodeEvent, Output, Run, WorkflowDef } from "../api/types";
import { RunDetail } from "./RunDetail";

vi.mock("../api/endpoints");
import {
  getRun,
  getRunOutput,
  getRunProgress,
  getRunReview,
  getWorkflowDef,
} from "../api/endpoints";

const NEWS_DEF: WorkflowDef = {
  def_id: "news",
  name: "news",
  builtin: true,
  created_at: null,
  updated_at: null,
  description: null,
  definition: {
    id: "news",
    entry: "summarize",
    params: {},
    output_ref: "digest",
    nodes: [
      {
        id: "summarize",
        kind: "step",
        handler_ref: "digest_summarize",
        branch: { predicate_ref: "p", routes: { verify: "verify", end: "__end__" } },
      },
      { id: "verify", kind: "step", handler_ref: "digest_verify", next: "__end__" },
    ],
  },
};

const SUCCESS_RUN: Run = {
  id: "r1",
  workflow: "news",
  status: "success",
  trigger: "manual",
  created_at: "2026-06-08T00:00:00Z",
  started_at: "2026-06-08T00:00:01Z",
  finished_at: "2026-06-08T00:00:05Z",
  error: null,
};

const DIGEST: Output = {
  id: "o1",
  run_id: "r1",
  type: "digest",
  content: "# Daily Digest\n\n- [Big news](https://example.com/a) — it happened",
  data: null,
  created_at: "2026-06-08T00:00:05Z",
};

function renderDetail(id = "r1") {
  return render(
    <MemoryRouter
      initialEntries={[`/runs/${id}`]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/runs/:id" element={<RunDetail />} />
        <Route path="/" element={<div>RUNS HOME</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RunDetail", () => {
  beforeEach(() => {
    vi.mocked(getRun).mockReset();
    vi.mocked(getRunOutput).mockReset();
    // useRun also fetches the workflow def (for the graph) + the progress timeline;
    // default them so the fire-and-forget calls resolve cleanly.
    vi.mocked(getWorkflowDef).mockResolvedValue(NEWS_DEF);
    vi.mocked(getRunProgress).mockResolvedValue([]);
    vi.mocked(getRunReview).mockResolvedValue({});
  });
  afterEach(() => vi.clearAllMocks());

  it("renders run metadata and the digest as formatted markdown", async () => {
    vi.mocked(getRun).mockResolvedValue(SUCCESS_RUN);
    vi.mocked(getRunOutput).mockResolvedValue([DIGEST]);

    renderDetail();

    // status + metadata
    expect(await screen.findByText("Success")).toBeInTheDocument();
    // markdown -> a real heading and a hardened external link
    expect(await screen.findByRole("heading", { name: /daily digest/i })).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /big news/i });
    expect(link).toHaveAttribute("href", "https://example.com/a");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("shows a not-found message for a missing run", async () => {
    vi.mocked(getRun).mockRejectedValue(new ApiError(404, "run not found"));
    renderDetail("ghost");
    expect(await screen.findByText(/run not found/i)).toBeInTheDocument();
  });

  // Phase 10b-2: a finished coding run shows the shell commands it ran (side effects
  // not in the diff) and a prominent banner if it touched .git.
  const CODING_OUTPUT: Output = {
    id: "o2",
    run_id: "r1",
    type: "coding",
    content: "# Coding run",
    data: {
      summary: "added hello()",
      diff: "--- a/hello.py\n+++ b/hello.py\n+def hello():\n",
      changed_files: ["hello.py"],
      status: "completed",
      commands: ["python -m pytest -q", "ruff check ."],
      git_tampered: [],
    },
    created_at: "2026-06-08T00:00:05Z",
  };

  it("renders a coding run's commands alongside the diff", async () => {
    vi.mocked(getRun).mockResolvedValue({ ...SUCCESS_RUN, workflow: "coding" });
    vi.mocked(getRunOutput).mockResolvedValue([CODING_OUTPUT]);

    renderDetail();

    const commands = await screen.findByLabelText("commands");
    expect(commands).toHaveTextContent("python -m pytest -q");
    expect(commands).toHaveTextContent("ruff check .");
    expect(screen.getByLabelText("diff")).toHaveTextContent("+def hello():");
  });

  it("flags a .git tampering attempt on a finished coding run", async () => {
    vi.mocked(getRun).mockResolvedValue({
      ...SUCCESS_RUN, workflow: "coding", status: "failed", error: ".git tampered (reverted)",
    });
    vi.mocked(getRunOutput).mockResolvedValue([
      { ...CODING_OUTPUT, data: { ...CODING_OUTPUT.data, git_tampered: ["hooks/pre-commit"] } },
    ]);

    renderDetail();

    expect(await screen.findByText(/git internals/i)).toBeInTheDocument();
    expect(screen.getByText(/hooks\/pre-commit/)).toBeInTheDocument();
  });

  it("draws the workflow graph and overlays live per-node status", async () => {
    const events: NodeEvent[] = [
      { node_id: "summarize", status: "done", seq: 0, at: "2026-06-08T00:00:01Z" },
      { node_id: "verify", status: "running", seq: 1, at: "2026-06-08T00:00:02Z" },
    ];
    vi.mocked(getRun).mockResolvedValue({ ...SUCCESS_RUN, status: "running" });
    vi.mocked(getRunOutput).mockResolvedValue([]);
    vi.mocked(getRunProgress).mockResolvedValue(events);

    const { container } = renderDetail();

    // the graph renders + the live tag, and the running node shows a pulse indicator
    expect(await screen.findByText("● live")).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: /workflow graph/i })).toBeInTheDocument();
    await vi.waitFor(() => expect(container.querySelector("circle")).toBeInTheDocument());
  });
});
