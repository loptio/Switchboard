// Typed wrappers over apiFetch — one function per Unit 1 endpoint. Components
// call these, never apiFetch directly, so the contract lives in one place.
import { apiFetch } from "./client";
import type {
  AgentDef,
  Manifest,
  Output,
  ReviewPayload,
  Run,
  RunStatus,
  Schedule,
  User,
  WorkflowDef,
} from "./types";

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

/** The human-review payload for an awaiting_input run (or {} if none). */
export const getRunReview = (id: string) => apiFetch<ReviewPayload>(`/runs/${id}/review`);

/** Manual trigger — enqueues a pending run (202); the worker executes it.
 *  `review` requests the human-review gate (digest family). */
export const triggerRun = (workflow?: string, review = false) =>
  apiFetch<Run>("/runs", {
    method: "POST",
    body: workflow || review ? { workflow: workflow ?? "news", review } : undefined,
  });

/** Approve / redo an awaiting_input run — the web resume handoff. */
export const resumeRun = (id: string, action: "approve" | "redo", feedback?: string) =>
  apiFetch<Run>(`/runs/${id}/resume`, { method: "POST", body: { action, feedback } });

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

// --- workflow / agent definitions (Phase 8 synthesizer) ---
export interface DefBody {
  definition: Record<string, unknown>;
  name?: string | null;
  description?: string | null;
}

export const listWorkflowDefs = () => apiFetch<WorkflowDef[]>("/workflows");
export const getWorkflowDef = (id: string) => apiFetch<WorkflowDef>(`/workflows/${id}`);
export const createWorkflowDef = (body: DefBody) =>
  apiFetch<WorkflowDef>("/workflows", { method: "POST", body });
export const updateWorkflowDef = (id: string, body: Partial<DefBody>) =>
  apiFetch<WorkflowDef>(`/workflows/${id}`, { method: "PATCH", body });
export const cloneWorkflowDef = (id: string, newId: string, name?: string) =>
  apiFetch<WorkflowDef>(`/workflows/${id}/clone`, {
    method: "POST",
    body: { new_id: newId, name },
  });
export const deleteWorkflowDef = (id: string) =>
  apiFetch<void>(`/workflows/${id}`, { method: "DELETE" });

export const listAgentDefs = () => apiFetch<AgentDef[]>("/agents");
export const getAgentDef = (id: string) => apiFetch<AgentDef>(`/agents/${id}`);
export const createAgentDef = (body: DefBody) =>
  apiFetch<AgentDef>("/agents", { method: "POST", body });
export const updateAgentDef = (id: string, body: Partial<DefBody>) =>
  apiFetch<AgentDef>(`/agents/${id}`, { method: "PATCH", body });
export const cloneAgentDef = (id: string, newId: string, name?: string) =>
  apiFetch<AgentDef>(`/agents/${id}/clone`, {
    method: "POST",
    body: { new_id: newId, name },
  });
export const deleteAgentDef = (id: string) =>
  apiFetch<void>(`/agents/${id}`, { method: "DELETE" });

export const getComponents = () => apiFetch<Manifest>("/components");
