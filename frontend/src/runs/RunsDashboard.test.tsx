import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Run } from "../api/types";
import { RunsDashboard } from "./RunsDashboard";
import { pollConfig } from "./useRuns";

vi.mock("../api/endpoints");
import { listRuns, triggerRun } from "../api/endpoints";

function makeRun(over: Partial<Run> = {}): Run {
  return {
    id: "r1",
    workflow: "news",
    status: "pending",
    trigger: "manual",
    // Must be recent: useRuns hard-stops polling for non-terminal runs older
    // than 5 minutes, so a fixed date here silently disables the poll loop.
    created_at: new Date().toISOString(),
    started_at: null,
    finished_at: null,
    error: null,
    ...over,
  };
}

function renderDashboard() {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <RunsDashboard />
    </MemoryRouter>,
  );
}

describe("RunsDashboard", () => {
  beforeEach(() => {
    vi.mocked(listRuns).mockResolvedValue([]);
    vi.mocked(triggerRun).mockReset();
  });
  afterEach(() => {
    vi.clearAllMocks();
    pollConfig.intervalMs = 2000;
  });

  it("shows the empty state when there are no runs", async () => {
    renderDashboard();
    expect(await screen.findByText(/no runs yet/i)).toBeInTheDocument();
  });

  it("renders runs with status badges", async () => {
    vi.mocked(listRuns).mockResolvedValue([
      makeRun({ id: "a", status: "success" }),
      makeRun({ id: "b", status: "failed" }),
    ]);
    renderDashboard();
    expect(await screen.findByText("Success")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("'Run now' triggers a run and shows it optimistically", async () => {
    const user = userEvent.setup();
    vi.mocked(listRuns).mockResolvedValue([]);
    vi.mocked(triggerRun).mockResolvedValue(makeRun({ id: "new", status: "pending" }));

    renderDashboard();
    await screen.findByText(/no runs yet/i);
    await user.click(screen.getByRole("button", { name: /run now/i }));

    expect(await screen.findByText("Pending")).toBeInTheDocument();
    expect(triggerRun).toHaveBeenCalledOnce();
  });

  it("triggers a chosen workflow with the review gate (coding)", async () => {
    const user = userEvent.setup();
    vi.mocked(listRuns).mockResolvedValue([]);
    vi.mocked(triggerRun).mockResolvedValue(makeRun({ id: "c1", workflow: "coding" }));

    renderDashboard();
    await screen.findByText(/no runs yet/i);
    await user.type(screen.getByLabelText(/workflow to run/i), "coding");
    await user.click(screen.getByRole("checkbox", { name: /review/i }));
    await user.click(screen.getByRole("button", { name: /run now/i }));

    // No per-run task/workspace typed -> coding fields omitted (Config fallback).
    await waitFor(() => expect(triggerRun).toHaveBeenCalledWith("coding", true, undefined));
  });

  it("sends the per-run task + workspace for a coding run (Phase 10b-1)", async () => {
    const user = userEvent.setup();
    vi.mocked(listRuns).mockResolvedValue([]);
    vi.mocked(triggerRun).mockResolvedValue(makeRun({ id: "c2", workflow: "coding" }));

    renderDashboard();
    await screen.findByText(/no runs yet/i);
    // The task/workspace fields appear only once the coding workflow is selected.
    await user.type(screen.getByLabelText(/workflow to run/i), "coding");
    await user.type(screen.getByLabelText(/coding task/i), "add a hello module");
    await user.type(screen.getByLabelText(/coding workspace/i), "/repos/proj");
    await user.click(screen.getByRole("button", { name: /run now/i }));

    await waitFor(() =>
      expect(triggerRun).toHaveBeenCalledWith("coding", false, {
        coding_task: "add a hello module",
        coding_workspace: "/repos/proj",
      }),
    );
  });

  it("polls until a pending run reaches success", async () => {
    pollConfig.intervalMs = 15; // poll fast under real timers
    vi.mocked(listRuns)
      .mockResolvedValueOnce([makeRun({ id: "r1", status: "pending" })])
      .mockResolvedValue([makeRun({ id: "r1", status: "success" })]);

    renderDashboard();
    expect(await screen.findByText("Pending")).toBeInTheDocument();
    // A later poll reports success → the UI reflects it with no user action.
    await waitFor(() => expect(screen.getByText("Success")).toBeInTheDocument());
  });
});
