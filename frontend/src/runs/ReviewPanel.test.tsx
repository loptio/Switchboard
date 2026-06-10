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

const CODING_REVIEW: ReviewPayload = {
  coding: {
    summary: "added a hello() function",
    diff: "--- a/hello.py\n+++ b/hello.py\n+def hello():\n",
    changed_files: ["hello.py"],
    status: "completed",
  },
};

describe("ReviewPanel (coding diff)", () => {
  beforeEach(() => vi.mocked(resumeRun).mockResolvedValue({ id: "r1" } as Run));
  afterEach(() => vi.clearAllMocks());

  it("renders the coding diff, summary and changed files", () => {
    render(<ReviewPanel runId="r1" review={CODING_REVIEW} onResolved={() => {}} />);
    expect(screen.getByText(/added a hello\(\) function/)).toBeInTheDocument();
    expect(screen.getByText("hello.py")).toBeInTheDocument();
    expect(screen.getByLabelText("diff")).toHaveTextContent("+def hello():");
  });

  it("flags a bounded stopped_limit run", () => {
    const stopped: ReviewPayload = {
      coding: { ...CODING_REVIEW.coding!, status: "stopped_limit" },
    };
    render(<ReviewPanel runId="r1" review={stopped} onResolved={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/stopped_limit/);
  });

  it("approves a coding diff via the resume handoff", async () => {
    const user = userEvent.setup();
    render(<ReviewPanel runId="r1" review={CODING_REVIEW} onResolved={() => {}} />);
    await user.click(screen.getByRole("button", { name: /approve/i }));
    await waitFor(() => expect(resumeRun).toHaveBeenCalledWith("r1", "approve", undefined));
  });

  it("shows the shell commands the agent ran (Phase 10b-2)", () => {
    const withCmds: ReviewPayload = {
      coding: { ...CODING_REVIEW.coding!, commands: ["python -m pytest -q", "ruff check ."] },
    };
    render(<ReviewPanel runId="r1" review={withCmds} onResolved={() => {}} />);
    const list = screen.getByLabelText("commands");
    expect(list).toHaveTextContent("python -m pytest -q");
    expect(list).toHaveTextContent("ruff check .");
  });

  it("prominently flags a .git tampering attempt (Phase 10b-2)", () => {
    const tampered: ReviewPayload = {
      coding: { ...CODING_REVIEW.coding!, git_tampered: ["hooks/pre-commit"] },
    };
    render(<ReviewPanel runId="r1" review={tampered} onResolved={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/git internals/i);
    expect(screen.getByRole("alert")).toHaveTextContent(/hooks\/pre-commit/);
  });
});

const META_REVIEW: ReviewPayload = {
  proposal: {
    request: "make me a stern digest variant",
    workflow_def: { id: "stern-news", entry: "summarize" },
    agent_defs: [{ id: "stern-summarize", parser_ref: "parse_digest" }],
    explanation: "克隆 digest 并替换 summarizer 提示词",
    attempts: 1,
  },
};

describe("ReviewPanel (meta proposal)", () => {
  beforeEach(() => vi.mocked(resumeRun).mockResolvedValue({ id: "r1" } as Run));
  afterEach(() => vi.clearAllMocks());

  it("renders the request, explanation and proposed defs", () => {
    render(<ReviewPanel runId="r1" review={META_REVIEW} onResolved={() => {}} />);
    expect(screen.getByText(/stern digest variant/)).toBeInTheDocument();
    expect(screen.getByText(/克隆 digest/)).toBeInTheDocument();
    expect(screen.getByLabelText("proposed workflow def").textContent).toContain("stern-news");
    expect(screen.getByLabelText("proposed agent def").textContent).toContain("stern-summarize");
    // approving creates defs — the panel says so up front
    expect(screen.getByRole("alert")).toHaveTextContent(/create these definitions/i);
  });

  it("approve flows through the same resume handoff", async () => {
    const user = userEvent.setup();
    render(<ReviewPanel runId="r1" review={META_REVIEW} onResolved={() => {}} />);
    await user.click(screen.getByRole("button", { name: /approve/i }));
    await waitFor(() => expect(resumeRun).toHaveBeenCalledWith("r1", "approve", undefined));
  });
});
