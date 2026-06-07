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
    created_at: "2026-06-08T00:00:00Z",
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
