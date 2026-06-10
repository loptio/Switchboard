import { useState } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { triggerRun } from "../api/endpoints";
import { Button } from "../components/Button";
import { ErrorBanner } from "../components/ErrorBanner";
import { Spinner } from "../components/Spinner";
import { formatTime } from "../lib/format";
import { RunStatusBadge } from "./RunStatusBadge";
import { useRuns } from "./useRuns";
import styles from "./RunsDashboard.module.css";

export function RunsDashboard() {
  const { runs, loading, error, softHint, refresh, addOptimistic } = useRuns();
  const [triggering, setTriggering] = useState(false);
  const [triggerError, setTriggerError] = useState<string | null>(null);
  // Minimal trigger control (Phase 10a): pick a workflow (blank = the default news
  // digest) and optionally request the human-review gate — needed to drive a coding
  // run with diff review end-to-end from the web.
  const [workflow, setWorkflow] = useState("");
  const [review, setReview] = useState(false);
  // Per-run coding intake (Phase 10b-1): a coding run carries its own task + workspace
  // (a real git repo). Shown only when the coding workflow is selected; blank fields
  // fall back to the worker's Config (CODING_TASK / CODING_WORKSPACE).
  const [codingTask, setCodingTask] = useState("");
  const [codingWorkspace, setCodingWorkspace] = useState("");
  const isCoding = workflow.trim() === "coding";
  // Phase 9: a meta run's natural-language request rides the same per-run task pipe;
  // review is MANDATORY for meta (the worker refuses a gateless meta run), so the
  // trigger forces the flag on rather than letting it fail server-side.
  const isMeta = workflow.trim() === "meta";

  async function onRunNow() {
    setTriggering(true);
    setTriggerError(null);
    try {
      const task = codingTask.trim();
      const ws = codingWorkspace.trim();
      const coding =
        isCoding && (task || ws)
          ? { coding_task: task || undefined, coding_workspace: ws || undefined }
          : isMeta && task
            ? { coding_task: task }
            : undefined;
      const run = await triggerRun(workflow.trim() || undefined, review || isMeta, coding);
      addOptimistic(run); // show it immediately; polling will track status
    } catch (e) {
      setTriggerError(e instanceof ApiError ? e.detail : "Failed to trigger a run.");
    } finally {
      setTriggering(false);
    }
  }

  return (
    <section>
      <div className={styles.toolbar}>
        <h1 className={styles.title}>Runs</h1>
        <div className={styles.actions}>
          <input
            aria-label="Workflow to run"
            placeholder="workflow (default news)"
            value={workflow}
            onChange={(e) => setWorkflow(e.target.value)}
            className={styles.workflowInput}
          />
          <label className={styles.reviewToggle}>
            <input
              type="checkbox"
              checked={review}
              onChange={(e) => setReview(e.target.checked)}
            />
            review
          </label>
          <Button variant="secondary" onClick={() => void refresh()}>
            Refresh
          </Button>
          <Button onClick={() => void onRunNow()} disabled={triggering}>
            {triggering ? "Starting…" : "Run now"}
          </Button>
        </div>
      </div>

      {(isCoding || isMeta) && (
        <div className={styles.codingFields}>
          <textarea
            aria-label={isMeta ? "Meta request" : "Coding task"}
            placeholder={
              isMeta
                ? "describe the workflow you want — the meta-agent drafts it for your review"
                : "coding task for this run (blank = CODING_TASK env)"
            }
            value={codingTask}
            onChange={(e) => setCodingTask(e.target.value)}
            className={styles.codingTask}
          />
          {isCoding && (
            <input
              aria-label="Coding workspace"
              placeholder="workspace path — a git repo (blank = CODING_WORKSPACE env)"
              value={codingWorkspace}
              onChange={(e) => setCodingWorkspace(e.target.value)}
              className={styles.codingWorkspace}
            />
          )}
        </div>
      )}

      {triggerError && <ErrorBanner message={triggerError} />}
      {error && <ErrorBanner message={error} />}
      {softHint && (
        <div className={styles.hint} role="status">
          A run has been pending for a while — make sure the worker (
          <code>cli.py scheduler</code>) is running.
        </div>
      )}

      {loading ? (
        <div className={styles.center}>
          <Spinner label="Loading runs…" />
        </div>
      ) : runs.length === 0 ? (
        <div className={styles.empty}>
          No runs yet. Click <strong>Run now</strong> to start one.
        </div>
      ) : (
        <div className={styles.list}>
          <div className={styles.head}>
            <span>Status</span>
            <span>Workflow</span>
            <span>Trigger</span>
            <span>Created</span>
            <span />
          </div>
          {runs.map((run) => (
            <div key={run.id} className={styles.row}>
              <div className={styles.cellStatus}>
                <RunStatusBadge status={run.status} />
              </div>
              <div className={styles.cellWorkflow}>{run.workflow}</div>
              <div className={styles.cellTrigger}>{run.trigger}</div>
              <div className={styles.cellCreated}>{formatTime(run.created_at)}</div>
              <div className={styles.cellAction}>
                <Link to={`/runs/${run.id}`}>View</Link>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
