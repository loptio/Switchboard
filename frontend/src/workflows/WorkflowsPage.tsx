import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { cloneWorkflowDef, deleteWorkflowDef, triggerRun } from "../api/endpoints";
import type { WorkflowDef } from "../api/types";
import { Button } from "../components/Button";
import { ErrorBanner } from "../components/ErrorBanner";
import { Spinner } from "../components/Spinner";
import { useWorkflows } from "./useWorkflows";
import { WorkflowGraph } from "./WorkflowGraph";
import styles from "./WorkflowsPage.module.css";

/** Master-detail workflows view: a list on the left, the selected workflow's graph +
 *  actions (Run, Clone, Edit, Delete) on the right. Built-in defs are read-only —
 *  clone one to create an editable variant. */
export function WorkflowsPage() {
  const { items, loading, error, refresh } = useWorkflows();
  const [msg, setMsg] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const navigate = useNavigate();

  // Keep a valid selection: default to the first workflow, and re-resolve if the
  // selected one disappears (e.g. after a delete) or the list loads/changes.
  const selected = useMemo(
    () => items.find((w) => w.def_id === selectedId) ?? items[0] ?? null,
    [items, selectedId],
  );
  useEffect(() => {
    if (selected && selected.def_id !== selectedId) setSelectedId(selected.def_id);
  }, [selected, selectedId]);

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
      if (selectedId === defId) setSelectedId(null);
      await refresh();
    } catch (e) {
      setActionError(e instanceof ApiError ? e.detail : "Failed to delete.");
    }
  }

  return (
    <section className={styles.page}>
      <h1 className={styles.title}>Workflows</h1>
      <p className={styles.muted}>
        Pick a workflow to see its graph. Built-in workflows are read-only — clone one
        to create an editable variant.
      </p>
      {msg && <p className={styles.muted}>{msg}</p>}
      {error && <ErrorBanner message={error} />}
      {actionError && <ErrorBanner message={actionError} />}

      {loading ? (
        <div className={styles.center}>
          <Spinner label="Loading workflows…" />
        </div>
      ) : items.length === 0 ? (
        <div className={styles.empty}>No workflows yet.</div>
      ) : (
        <div className={styles.layout}>
          <nav className={styles.sidebar} aria-label="workflows">
            {items.map((wf) => (
              <button
                key={wf.def_id}
                type="button"
                className={`${styles.item} ${
                  selected?.def_id === wf.def_id ? styles.itemActive : ""
                }`}
                aria-current={selected?.def_id === wf.def_id}
                onClick={() => setSelectedId(wf.def_id)}
              >
                <span className={styles.itemName}>{wf.name || wf.def_id}</span>
                <span
                  className={`${styles.badge} ${wf.builtin ? styles.builtinBadge : ""}`}
                >
                  {wf.builtin ? "built-in" : "custom"}
                </span>
              </button>
            ))}
          </nav>

          {selected && <WorkflowDetail wf={selected} onRun={run} onClone={clone} onRemove={remove} navigate={navigate} />}
        </div>
      )}
    </section>
  );
}

function WorkflowDetail({
  wf,
  onRun,
  onClone,
  onRemove,
  navigate,
}: {
  wf: WorkflowDef;
  onRun: (id: string, review?: boolean) => void;
  onClone: (id: string) => void;
  onRemove: (id: string) => void;
  navigate: (to: string) => void;
}) {
  const family = wf.definition.output_ref;
  const reviewable = family === "digest" || family === "coding";
  return (
    <main className={styles.detail}>
      <div className={styles.detailHead}>
        <div className={styles.detailTitleWrap}>
          <h2 className={styles.detailTitle}>{wf.name || wf.def_id}</h2>
          <span className={styles.metaLine}>
            <code>{wf.def_id}</code>
            {family && <span className={styles.badge}>{family}</span>}
            <span className={styles.muted}>{wf.definition.nodes.length} node(s)</span>
          </span>
        </div>
        <div className={styles.detailActions}>
          <Button onClick={() => onRun(wf.def_id)}>Run now</Button>
          {reviewable && (
            <Button variant="secondary" onClick={() => onRun(wf.def_id, true)}>
              Run (review)
            </Button>
          )}
          <Button variant="secondary" onClick={() => onClone(wf.def_id)}>
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
              <Button variant="danger" onClick={() => onRemove(wf.def_id)}>
                Delete
              </Button>
            </>
          )}
        </div>
      </div>

      {wf.description && <p className={styles.description}>{wf.description}</p>}

      <div className={styles.graphWrap}>
        <WorkflowGraph definition={wf.definition} />
      </div>
    </main>
  );
}
