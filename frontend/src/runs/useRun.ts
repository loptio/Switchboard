import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { getRun, getRunOutput } from "../api/endpoints";
import { isTerminal } from "../api/types";
import type { Output, Run } from "../api/types";
import { pollConfig } from "./useRuns";

export interface UseRun {
  run: Run | null;
  outputs: Output[];
  loading: boolean;
  error: string | null;
  notFound: boolean;
}

/** One run + its outputs, polling while the run is still non-terminal. */
export function useRun(id: string): UseRun {
  const [run, setRun] = useState<Run | null>(null);
  const [outputs, setOutputs] = useState<Output[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  const mounted = useRef(true);
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
      if (r.status === "success") {
        const outs = await getRunOutput(id);
        if (mounted.current) setOutputs(outs);
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

  return { run, outputs, loading, error, notFound };
}
