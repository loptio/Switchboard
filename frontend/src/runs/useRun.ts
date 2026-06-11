import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import {
  getRun,
  getRunOutput,
  getRunProgress,
  getRunReview,
  getWorkflowDef,
} from "../api/endpoints";
import { isTerminal } from "../api/types";
import type { NodeEvent, Output, ReviewPayload, Run, WorkflowDefinition } from "../api/types";
import type { NodeRunState } from "../workflows/WorkflowGraph";
import { pollConfig } from "./useRuns";

export interface UseRun {
  run: Run | null;
  outputs: Output[];
  review: ReviewPayload | null;
  /** The run's workflow definition (for the graph), once resolved. */
  definition: WorkflowDefinition | null;
  /** Latest run-state per node id, derived from the event timeline (Phase 11). */
  nodeStatuses: Record<string, NodeRunState>;
  loading: boolean;
  error: string | null;
  notFound: boolean;
  reload: () => Promise<void>;
}

// awaiting (suspended at a gate) reads as an active node in the graph.
function toRunState(s: NodeEvent["status"]): NodeRunState {
  return s === "awaiting" ? "running" : s;
}

/** Latest status per node, by event order (events arrive seq-ordered). */
function deriveStatuses(events: NodeEvent[]): Record<string, NodeRunState> {
  const out: Record<string, NodeRunState> = {};
  for (const e of events) out[e.node_id] = toRunState(e.status);
  return out;
}

/** One run + its outputs, polling while the run is still non-terminal. */
export function useRun(id: string): UseRun {
  const [run, setRun] = useState<Run | null>(null);
  const [outputs, setOutputs] = useState<Output[]>([]);
  const [review, setReview] = useState<ReviewPayload | null>(null);
  const [definition, setDefinition] = useState<WorkflowDefinition | null>(null);
  const [nodeStatuses, setNodeStatuses] = useState<Record<string, NodeRunState>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  const mounted = useRef(true);
  const defLoadedFor = useRef<string | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    try {
      const r = await getRun(id);
      if (!mounted.current) return;
      setRun(r);
      setError(null);
      setNotFound(false);

      // The workflow definition (for the graph) — fetch once per workflow, best-effort
      // (a meta-created def the run names may have been deleted; that's not fatal).
      if (defLoadedFor.current !== r.workflow) {
        defLoadedFor.current = r.workflow;
        getWorkflowDef(r.workflow)
          .then((wf) => mounted.current && setDefinition(wf.definition))
          .catch(() => mounted.current && setDefinition(null));
      }
      // The per-node event timeline → latest status per node (live graph overlay).
      getRunProgress(id)
        .then((evs) => mounted.current && setNodeStatuses(deriveStatuses(evs)))
        .catch(() => {});

      if (r.status === "success" || r.status === "failed") {
        // Failed runs can still have a persisted output worth showing — e.g. a coding
        // run refused for .git tampering, or a bounded stop with a partial diff/commands.
        const outs = await getRunOutput(id);
        if (mounted.current) setOutputs(outs);
      } else if (r.status === "awaiting_input") {
        const rev = await getRunReview(id);
        if (mounted.current) setReview(rev);
      }
    } catch (e) {
      if (!mounted.current) return;
      if (e instanceof ApiError && e.status === 404) setNotFound(true);
      else setError(e instanceof ApiError ? e.detail : "Failed to load run.");
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    setLoading(true);
    void load();
  }, [load]);

  const polling = run != null && !isTerminal(run.status);
  useEffect(() => {
    if (!polling) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const loop = () => {
      timer = setTimeout(async () => {
        if (cancelled) return;
        if (document.visibilityState === "visible") await load();
        if (!cancelled) loop();
      }, pollConfig.intervalMs);
    };
    loop();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [polling, load]);

  return {
    run,
    outputs,
    review,
    definition,
    nodeStatuses,
    loading,
    error,
    notFound,
    reload: load,
  };
}
