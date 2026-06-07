import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { listSchedules } from "../api/endpoints";
import type { Schedule } from "../api/types";

export interface UseSchedules {
  schedules: Schedule[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

/** Schedules list + a refresh to call after a create/update/delete. */
export function useSchedules(): UseSchedules {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const list = await listSchedules();
      if (!mounted.current) return;
      setSchedules(list);
      setError(null);
    } catch (e) {
      if (mounted.current) {
        setError(e instanceof ApiError ? e.detail : "Failed to load schedules.");
      }
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { schedules, loading, error, refresh };
}
