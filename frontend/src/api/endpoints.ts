// Typed wrappers over apiFetch — one function per Unit 1 endpoint. Components
// call these, never apiFetch directly, so the contract lives in one place.
import { apiFetch } from "./client";
import type { Output, Run, RunStatus, Schedule, User } from "./types";

// --- auth ---
export const login = (username: string, password: string) =>
  apiFetch<User>("/auth/login", { method: "POST", body: { username, password } });

export const logout = () => apiFetch<void>("/auth/logout", { method: "POST" });

export const getMe = () => apiFetch<User>("/auth/me");

// --- runs ---
export interface ListRunsParams {
  status?: RunStatus;
  workflow?: string;
  limit?: number;
}

export function listRuns(params: ListRunsParams = {}): Promise<Run[]> {
  const q = new URLSearchParams();
  if (params.status) q.set("status", params.status);
  if (params.workflow) q.set("workflow", params.workflow);
  if (params.limit != null) q.set("limit", String(params.limit));
  const qs = q.toString();
  return apiFetch<Run[]>(`/runs${qs ? `?${qs}` : ""}`);
}

export const getRun = (id: string) => apiFetch<Run>(`/runs/${id}`);

export const getRunOutput = (id: string) => apiFetch<Output[]>(`/runs/${id}/output`);

/** Manual trigger — enqueues a pending run (202); the worker executes it. */
export const triggerRun = (workflow?: string) =>
  apiFetch<Run>("/runs", { method: "POST", body: workflow ? { workflow } : undefined });

// --- schedules ---
export const listSchedules = () => apiFetch<Schedule[]>("/schedules");

export interface ScheduleCreate {
  cron: string;
  workflow?: string;
  tz?: string;
  enabled?: boolean;
}

export const createSchedule = (body: ScheduleCreate) =>
  apiFetch<Schedule>("/schedules", { method: "POST", body });

export interface ScheduleUpdate {
  cron?: string;
  tz?: string;
  enabled?: boolean;
}

export const updateSchedule = (id: string, body: ScheduleUpdate) =>
  apiFetch<Schedule>(`/schedules/${id}`, { method: "PATCH", body });

export const deleteSchedule = (id: string) =>
  apiFetch<void>(`/schedules/${id}`, { method: "DELETE" });
