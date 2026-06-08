import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { cloneAgentDef, deleteAgentDef } from "../api/endpoints";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ErrorBanner } from "../components/ErrorBanner";
import { Spinner } from "../components/Spinner";
import { useAgents } from "./useAgents";
import styles from "../workflows/Synth.module.css";

/** Lists built-in (read-only) + custom agent defs. Clone / edit prompt / delete. */
export function AgentsPage() {
  const { items, loading, error, refresh } = useAgents();
  const [actionError, setActionError] = useState<string | null>(null);
  const navigate = useNavigate();

  async function clone(id: string) {
    const newId = window.prompt(`Clone agent "${id}" as (new id):`, `${id}-copy`);
    if (!newId) return;
    setActionError(null);
    try {
      await cloneAgentDef(id, newId);
      await refresh();
      navigate(`/agents/${newId}/edit`);
    } catch (e) {
      setActionError(e instanceof ApiError ? e.detail : "Failed to clone.");
    }
  }

  async function remove(id: string) {
    if (!window.confirm(`Delete agent "${id}"?`)) return;
    setActionError(null);
    try {
      await deleteAgentDef(id);
      await refresh();
    } catch (e) {
      setActionError(e instanceof ApiError ? e.detail : "Failed to delete.");
    }
  }

  return (
    <section>
      <h1 className={styles.title}>Agents</h1>
      <p className={styles.muted}>
        Built-in agents are read-only — clone one to edit its prompt.
      </p>
      {error && <ErrorBanner message={error} />}
      {actionError && <ErrorBanner message={actionError} />}

      {loading ? (
        <div className={styles.center}>
          <Spinner label="Loading agents…" />
        </div>
      ) : (
        <div className={styles.list}>
          {items.map((a) => (
            <Card key={a.agent_id} className={styles.row}>
              <div className={styles.rowMain}>
                <span className={styles.defId}>
                  <span>{a.name || a.agent_id}</span>
                  <span
                    className={`${styles.badge} ${a.builtin ? styles.builtinBadge : ""}`}
                  >
                    {a.builtin ? "built-in" : "custom"}
                  </span>
                </span>
                <span className={styles.muted}>{a.agent_id}</span>
              </div>
              <div className={styles.actions}>
                <Button variant="secondary" onClick={() => void clone(a.agent_id)}>
                  Clone
                </Button>
                {!a.builtin && (
                  <>
                    <Button
                      variant="secondary"
                      onClick={() => navigate(`/agents/${a.agent_id}/edit`)}
                    >
                      Edit
                    </Button>
                    <Button variant="danger" onClick={() => void remove(a.agent_id)}>
                      Delete
                    </Button>
                  </>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}
    </section>
  );
}
