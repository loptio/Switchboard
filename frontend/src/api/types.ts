// Mirrors the Unit 1 OpenAPI contract (api/schemas.py). Datetimes arrive as ISO
// strings. Keep these in sync with the backend response models.

export type RunStatus = "pending" | "running" | "success" | "failed";
export type RunTrigger = "scheduled" | "manual";

export interface Run {
  id: string;
  workflow: string;
  status: RunStatus;
  trigger: RunTrigger;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export interface Output {
  id: string;
  run_id: string;
  type: string;
  content: string; // rendered markdown (the digest)
  data: Record<string, unknown> | null;
  created_at: string;
}

export interface Schedule {
  id: string;
  workflow: string;
  cron: string;
  timezone: string;
  enabled: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  created_at: string;
}

export interface User {
  username: string;
}

/** A run is "done" (stop polling) once it reaches a terminal status. */
export function isTerminal(status: RunStatus): boolean {
  return status === "success" || status === "failed";
}
