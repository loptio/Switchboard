import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { cloneWorkflowDef, deleteWorkflowDef, triggerRun } from "../api/endpoints";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ErrorBanner } from "../components/ErrorBanner";
import { Spinner } from "../components/Spinner";
import { useWorkflows } from "./useWorkflows";
import { WorkflowGraph } from "./WorkflowGraph";
import styles from "./Synth.module.css";

/** Lists built-in (read-only) + custom workflow defs. Run now / clone / edit / delete. */
export function WorkflowsPage() {
  const { items, loading, error, refresh } = useWorkflows();
  const [msg, setMsg] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [openGraph, setOpenGraph] = useState<string | null>(null);
  const navigate = useNavigate();

  async function run(defId: string, review = false) {
    setMsg(null);
    setActionError(null);
    try {
      const r = await triggerRun(defId, review);
      navigate(`/runs/${r.id}`);
    } catch (e) {
      setActionError(e instanceof ApiError ? e.detail : "Failed to run.");
    }
  }

  async function clone(defId: string) {
    const newId = window.prompt(`Clone "${defId}" as (new id):`, `${defId}-copy`);
    if (!newId) return;
    setActionError(null);
    try {
      await cloneWorkflowDef(defId, newId);
      await refresh();
      navigate(`/workflows/${newId}/edit`);
    } catch (e) {
      setActionError(e instanceof ApiError ? e.detail : "Failed to clone.");
    }
  }

  async function remove(defId: string) {
    if (!window.confirm(`Delete workflow "${defId}"?`)) return;
    setActionError(null);
    try {
      await deleteWorkflowDef(defId);
      await refresh();
    } catch (e) {
      setActionError(e instanceof ApiError ? e.detail : "Failed to delete.");
    }
  }

  return (
    <section>
      <h1 className={styles.title}>Workflows</h1>
      <p className={styles.muted}>
        Built-in workflows are read-only — clone one to create an editable variant.
      </p>
      {msg && <p className={styles.muted}>{msg}</p>}
      {error && <ErrorBanner message={error} />}
      {actionError && <ErrorBanner message={actionError} />}

      {loading ? (
        <div className={styles.center}>
          <Spinner label="Loading workflows…" />
        </div>
      ) : (
        <div className={styles.list}>
          {items.map((wf) => (
            <Card key={wf.def_id} className={styles.row}>
              <div className={styles.rowMain}>
                <span className={styles.defId}>
                  <span>{wf.name || wf.def_id}</span>
                  <span
                    className={`${styles.badge} ${wf.builtin ? styles.builtinBadge : ""}`}
                  >
                    {wf.builtin ? "built-in" : "custom"}
                  </span>
                  <span className={styles.badge}>{wf.definition.output_ref}</span>
                </span>
                <span className={styles.muted}>
                  {wf.def_id} · {wf.definition.nodes.length} node(s)
                </span>
              </div>
              <div className={styles.actions}>
                <Button onClick={() => void run(wf.def_id)}>Run now</Button>
                {(wf.definition.output_ref === "digest" ||
                  wf.definition.output_ref === "coding") && (
                  <Button variant="secondary" onClick={() => void run(wf.def_id, true)}>
                    Run (review)
                  </Button>
                )}
                <Button
                  variant="secondary"
                  aria-expanded={openGraph === wf.def_id}
                  onClick={() =>
                    setOpenGraph(openGraph === wf.def_id ? null : wf.def_id)
                  }
                >
                  {openGraph === wf.def_id ? "Hide graph" : "View graph"}
                </Button>
                <Button variant="secondary" onClick={() => void clone(wf.def_id)}>
                  Clone
                </Button>
                {!wf.builtin && (
                  <>
                    <Button
                      variant="secondary"
                      onClick={() => navigate(`/workflows/${wf.def_id}/edit`)}
                    >
                      Edit
                    </Button>
                    <Button variant="danger" onClick={() => void remove(wf.def_id)}>
                      Delete
                    </Button>
                  </>
                )}
              </div>
              {openGraph === wf.def_id && (
                <div className={styles.graphWrap}>
                  <WorkflowGraph definition={wf.definition} />
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </section>
  );
}
