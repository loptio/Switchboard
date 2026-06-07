import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { Output, Run } from "../api/types";
import { RunDetail } from "./RunDetail";

vi.mock("../api/endpoints");
import { getRun, getRunOutput } from "../api/endpoints";

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
});
