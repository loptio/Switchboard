import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { listWorkflowDefs } from "../api/endpoints";
import type { WorkflowDef } from "../api/types";

/** Workflow-def list + a refresh to call after a create/clone/delete. */
export function useWorkflows() {
  const [items, setItems] = useState<WorkflowDef[]>([]);
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
      const list = await listWorkflowDefs();
      if (!mounted.current) return;
      setItems(list);
      setError(null);
    } catch (e) {
      if (mounted.current) {
        setError(e instanceof ApiError ? e.detail : "Failed to load workflows.");
      }
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { items, loading, error, refresh };
}
