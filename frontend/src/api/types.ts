// Mirrors the Unit 1 OpenAPI contract (api/schemas.py). Datetimes arrive as ISO
// strings. Keep these in sync with the backend response models.

export type RunStatus =
  | "pending"
  | "running"
  | "success"
  | "failed"
  | "awaiting_input";
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

// --- Phase 8: workflow / agent definitions + the component manifest --------

export interface Branch {
  predicate_ref: string;
  routes: Record<string, string>;
}

export interface WfNode {
  id: string;
  kind: "step" | "human_review" | "fan_out" | "gather";
  handler_ref?: string;
  agent_ref?: string;
  config_key?: string;
  next?: string;
  branch?: Branch;
  over?: string;
  element_key?: string;
  body?: WfNode[];
  collect_ref?: string;
  into?: string;
  compose_ref?: string;
}

export interface WorkflowDefinition {
  id: string;
  entry: string;
  params: Record<string, unknown>;
  source_ref?: string;
  output_ref?: string;
  nodes: WfNode[];
}

export interface WorkflowDef {
  def_id: string;
  name: string | null;
  description: string | null;
  definition: WorkflowDefinition;
  builtin: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface AgentDefinition {
  id: string;
  system_prompt: string;
  prompt_builder_ref: string;
  parser_ref: string;
  model: string | null;
  params: Record<string, unknown>;
}

export interface AgentDef {
  agent_id: string;
  name: string | null;
  description: string | null;
  definition: AgentDefinition;
  builtin: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface Family {
  id: string;
  source: string;
  output: string;
  review: boolean;
  state: string;
}

// The human-review suspend payload (GET /runs/:id/review).
export interface ReviewIssue {
  index: number | null;
  kind: string;
  detail: string;
}
export interface ReviewPayload {
  digest?: { items: { title: string; link: string; one_line_summary: string }[] };
  issues?: ReviewIssue[];
}

export interface Manifest {
  node_kinds: Record<string, { requires: string[]; optional?: string[]; edge: string }>;
  node_handlers: string[];
  predicates: string[];
  composers: string[];
  agents: string[];
  prompt_builders: string[];
  parsers: string[];
  sources: string[];
  renderers: string[];
  families: Family[];
  end: string;
}
