import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ReviewPayload, Run } from "../api/types";
import { AssistantPage } from "./AssistantPage";

vi.mock("../api/endpoints");
import { getRun, getRunReview, resumeRun, triggerRun } from "../api/endpoints";

function run(over: Partial<Run> = {}): Run {
  return {
    id: "m1",
    workflow: "meta",
    status: "pending",
    trigger: "manual",
    created_at: new Date().toISOString(),
    started_at: null,
    finished_at: null,
    error: null,
    ...over,
  };
}

const PROPOSAL: ReviewPayload = {
  proposal: {
    request: "做一个只看科技视角的简报",
    workflow_def: { id: "tech-brief", entry: "filter", output_ref: "brief" },
    agent_defs: [{ id: "tech-filter" }],
    explanation: "基于 brief 家族重组",
    attempts: 1,
  },
};

function renderPage() {
  return render(
    <MemoryRouter>
      <AssistantPage />
    </MemoryRouter>,
  );
}

describe("AssistantPage", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(triggerRun).mockResolvedValue(run({ status: "pending" }));
    vi.mocked(resumeRun).mockResolvedValue(run({ status: "awaiting_input" }));
    vi.mocked(getRunReview).mockResolvedValue(PROPOSAL);
  });
  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("describe → draft → shows the proposal", async () => {
    const user = userEvent.setup();
    // worker: pending → running → awaiting_input
    vi.mocked(getRun)
      .mockResolvedValueOnce(run({ status: "running" }))
      .mockResolvedValue(run({ status: "awaiting_input" }));

    renderPage();
    await user.type(
      screen.getByLabelText(/describe the workflow you want/i),
      "做一个只看科技视角的简报",
    );
    await user.click(screen.getByRole("button", { name: /draft it/i }));

    // it triggers a meta run with the review gate and the request as the task
    await waitFor(() =>
      expect(triggerRun).toHaveBeenCalledWith("meta", true, {
        coding_task: "做一个只看科技视角的简报",
      }),
    );
    expect(await screen.findByText(/tech-brief/, {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByLabelText("proposed workflow def").textContent).toContain("tech-brief");
  });

  it("approve → creates the defs and shows links", async () => {
    const user = userEvent.setup();
    vi.mocked(getRun).mockResolvedValue(run({ status: "awaiting_input" }));

    renderPage();
    await user.type(screen.getByLabelText(/describe the workflow you want/i), "x");
    await user.click(screen.getByRole("button", { name: /draft it/i }));
    await screen.findByText(/tech-brief/, {}, { timeout: 4000 });

    // approve → run goes to success → "Created"
    vi.mocked(getRun).mockResolvedValue(run({ status: "success" }));
    await user.click(screen.getByRole("button", { name: /approve & create/i }));

    await waitFor(() => expect(resumeRun).toHaveBeenCalledWith("m1", "approve"), {
      timeout: 4000,
    });
    expect(await screen.findByText(/created/i, {}, { timeout: 4000 })).toBeInTheDocument();
  });

  it("refine sends feedback as a redo", async () => {
    const user = userEvent.setup();
    vi.mocked(getRun).mockResolvedValue(run({ status: "awaiting_input" }));

    renderPage();
    await user.type(screen.getByLabelText(/describe the workflow you want/i), "x");
    await user.click(screen.getByRole("button", { name: /draft it/i }));
    await screen.findByText(/tech-brief/, {}, { timeout: 4000 });

    await user.type(screen.getByLabelText(/refine with feedback/i), "换个名字");
    await user.click(screen.getByRole("button", { name: /refine with feedback/i }));
    await waitFor(() => expect(resumeRun).toHaveBeenCalledWith("m1", "redo", "换个名字"), {
      timeout: 4000,
    });
  });

  it("surfaces a failed run as an error", async () => {
    const user = userEvent.setup();
    vi.mocked(getRun).mockResolvedValue(run({ status: "failed", error: "boom" }));

    renderPage();
    await user.type(screen.getByLabelText(/describe the workflow you want/i), "x");
    await user.click(screen.getByRole("button", { name: /draft it/i }));
    expect(await screen.findByText(/boom/, {}, { timeout: 4000 })).toBeInTheDocument();
  });
});
