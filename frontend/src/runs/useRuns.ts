import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { listRuns } from "../api/endpoints";
import { isTerminal } from "../api/types";
import type { Run } from "../api/types";

// Poll cadence. Exposed as a mutable object so tests can shorten the interval;
// production always uses 2000ms.
export const pollConfig = { intervalMs: 2000 };
const SOFT_HINT_MS = 90_000; // pending longer than this → "is the worker up?"
const HARD_STOP_MS = 5 * 60_000; // stop auto-refresh after this (stuck → manual)

function ageMs(run: Run): number {
  return Date.now() - Date.parse(run.created_at);
}

export interface UseRuns {
  runs: Run[];
  loading: boolean;
  error: string | null;
  /** A non-terminal run has been pending too long — surface a soft warning. */
  softHint: boolean;
  /** Whether auto-refresh is currently active. */
  polling: boolean;
  refresh: () => Promise<void>;
  /** Show a just-triggered run immediately, before the next list fetch. */
  addOptimistic: (run: Run) => void;
}

/**
 * Runs list + reflect-async-status polling.
 *
 * Polls GET /runs every 2s while a non-terminal run is present, since the worker
 * picks runs up on a ~60s heartbeat. Stops when everything is terminal, when a
 * non-terminal run is older than the hard cap (stuck — likely no worker), or when
 * the tab is hidden. An optimistically-inserted run is keyed by id and dropped
 * once the server's list reports it (no duplicates).
 */
export function useRuns(): UseRuns {
  const [fetched, setFetched] = useState<Run[]>([]);
  const [optimistic, setOptimistic] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Don't setState after unmount (the async poll can resolve late).
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const list = await listRuns({ limit: 50 });
      if (!mounted.current) return;
      setFetched(list);
      setError(null);
      // Drop optimistic entries the server now knows about (dedup by id).
      setOptimistic((prev) => prev.filter((o) => !list.some((r) => r.id === o.id)));
    } catch (e) {
      if (!mounted.current) return;
      setError(e instanceof ApiError ? e.detail : "Failed to load runs.");
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  const addOptimistic = useCallback((run: Run) => {
    setOptimistic((prev) => [run, ...prev.filter((o) => o.id !== run.id)]);
  }, []);

  const runs = useMemo(() => {
    const ids = new Set(fetched.map((r) => r.id));
    return [...optimistic.filter((o) => !ids.has(o.id)), ...fetched];
  }, [fetched, optimistic]);

  const nonTerminal = runs.filter((r) => !isTerminal(r.status));
  const polling = nonTerminal.some((r) => ageMs(r) < HARD_STOP_MS);
  const softHint = nonTerminal.some((r) => r.status === "pending" && ageMs(r) > SOFT_HINT_MS);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!polling) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const loop = () => {
      timer = setTimeout(async () => {
        if (cancelled) return;
        if (document.visibilityState === "visible") {
          await refresh();
        }
        if (!cancelled) loop();
      }, pollConfig.intervalMs);
    };
    loop();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [polling, refresh]);

  return { runs, loading, error, softHint, polling, refresh, addOptimistic };
}
