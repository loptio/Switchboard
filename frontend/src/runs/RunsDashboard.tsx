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

  async function onRunNow() {
    setTriggering(true);
    setTriggerError(null);
    try {
      const run = await triggerRun();
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
          <Button variant="secondary" onClick={() => void refresh()}>
            Refresh
          </Button>
          <Button onClick={() => void onRunNow()} disabled={triggering}>
            {triggering ? "Starting…" : "Run now"}
          </Button>
        </div>
      </div>

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
