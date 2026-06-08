import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ReviewPayload, Run } from "../api/types";
import { ReviewPanel } from "./ReviewPanel";

vi.mock("../api/endpoints");
import { resumeRun } from "../api/endpoints";

const REVIEW: ReviewPayload = {
  digest: { items: [{ title: "T1", link: "https://e/1", one_line_summary: "summary one" }] },
  issues: [{ index: 1, kind: "human", detail: "tighten it" }],
};

describe("ReviewPanel", () => {
  beforeEach(() => vi.mocked(resumeRun).mockResolvedValue({ id: "r1" } as Run));
  afterEach(() => vi.clearAllMocks());

  it("renders the candidate and open issues", () => {
    render(<ReviewPanel runId="r1" review={REVIEW} onResolved={() => {}} />);
    expect(screen.getByText(/summary one/)).toBeInTheDocument();
    expect(screen.getByText(/tighten it/)).toBeInTheDocument();
  });

  it("approves via the resume handoff", async () => {
    const user = userEvent.setup();
    const onResolved = vi.fn();
    render(<ReviewPanel runId="r1" review={REVIEW} onResolved={onResolved} />);
    await user.click(screen.getByRole("button", { name: /approve/i }));
    await waitFor(() => expect(resumeRun).toHaveBeenCalledWith("r1", "approve", undefined));
    expect(onResolved).toHaveBeenCalled();
  });

  it("redoes with feedback", async () => {
    const user = userEvent.setup();
    render(<ReviewPanel runId="r1" review={REVIEW} onResolved={() => {}} />);
    await user.type(screen.getByLabelText(/feedback/i), "more detail");
    await user.click(screen.getByRole("button", { name: /redo/i }));
    await waitFor(() => expect(resumeRun).toHaveBeenCalledWith("r1", "redo", "more detail"));
  });
});
