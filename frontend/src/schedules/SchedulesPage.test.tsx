import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Schedule } from "../api/types";
import { SchedulesPage } from "./SchedulesPage";

vi.mock("../api/endpoints");
import { createSchedule, listSchedules, updateSchedule } from "../api/endpoints";

function makeSchedule(over: Partial<Schedule> = {}): Schedule {
  return {
    id: "s1",
    workflow: "news",
    cron: "0 6 * * *",
    timezone: "UTC",
    enabled: true,
    last_run_at: null,
    next_run_at: "2026-06-09T06:00:00Z",
    created_at: "2026-06-08T00:00:00Z",
    ...over,
  };
}

function renderPage() {
  return render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <SchedulesPage />
    </MemoryRouter>,
  );
}

describe("SchedulesPage", () => {
  beforeEach(() => {
    vi.mocked(listSchedules).mockResolvedValue([]);
    vi.mocked(createSchedule).mockReset();
    vi.mocked(updateSchedule).mockReset();
  });
  afterEach(() => vi.clearAllMocks());

  it("lists schedules", async () => {
    vi.mocked(listSchedules).mockResolvedValue([makeSchedule({ cron: "30 7 * * *" })]);
    renderPage();
    expect(await screen.findByText("30 7 * * *")).toBeInTheDocument();
    expect(screen.getByText("Enabled")).toBeInTheDocument();
  });

  it("creates a schedule from the form (default values)", async () => {
    const user = userEvent.setup();
    vi.mocked(createSchedule).mockResolvedValue(makeSchedule());
    renderPage();

    await screen.findByText(/no schedules yet/i);
    await user.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() =>
      expect(createSchedule).toHaveBeenCalledWith({
        cron: "0 6 * * *",
        tz: "UTC",
        workflow: "news",
      }),
    );
  });

  it("toggles a schedule's enabled state (with the CSRF-bearing PATCH)", async () => {
    const user = userEvent.setup();
    vi.mocked(listSchedules).mockResolvedValue([makeSchedule({ enabled: true })]);
    vi.mocked(updateSchedule).mockResolvedValue(makeSchedule({ enabled: false }));
    renderPage();

    await user.click(await screen.findByRole("button", { name: /disable/i }));

    await waitFor(() =>
      expect(updateSchedule).toHaveBeenCalledWith("s1", { enabled: false }),
    );
  });
});
