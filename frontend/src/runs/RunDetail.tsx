import { Link, useParams } from "react-router-dom";

import { Card } from "../components/Card";
import { ErrorBanner } from "../components/ErrorBanner";
import { Markdown } from "../components/Markdown";
import { Spinner } from "../components/Spinner";
import { formatTime } from "../lib/format";
import { RunStatusBadge } from "./RunStatusBadge";
import { useRun } from "./useRun";
import styles from "./RunDetail.module.css";

export function RunDetail() {
  const { id = "" } = useParams();
  const { run, outputs, loading, error, notFound } = useRun(id);

  if (loading) {
    return (
      <div className={styles.center}>
        <Spinner label="Loading run…" />
      </div>
    );
  }

  if (notFound) {
    return (
      <section>
        <Link to="/" className={styles.back}>
          ← Back to runs
        </Link>
        <div className={styles.empty}>Run not found.</div>
      </section>
    );
  }

  if (error) return <ErrorBanner message={error} />;
  if (!run) return null;

  const digest = outputs.find((o) => o.type === "digest") ?? outputs[0];
  const inProgress = run.status === "pending" || run.status === "running";

  return (
    <section>
      <Link to="/" className={styles.back}>
        ← Back to runs
      </Link>

      <div className={styles.header}>
        <RunStatusBadge status={run.status} />
        <h1 className={styles.title}>{run.workflow}</h1>
      </div>

      <dl className={styles.meta}>
        <div>
          <dt>Trigger</dt>
          <dd>{run.trigger}</dd>
        </div>
        <div>
          <dt>Created</dt>
          <dd>{formatTime(run.created_at)}</dd>
        </div>
        <div>
          <dt>Started</dt>
          <dd>{formatTime(run.started_at)}</dd>
        </div>
        <div>
          <dt>Finished</dt>
          <dd>{formatTime(run.finished_at)}</dd>
        </div>
      </dl>

      {run.error && <ErrorBanner message={run.error} />}

      <h2 className={styles.outputTitle}>Output</h2>
      {inProgress ? (
        <div className={styles.pending}>
          <Spinner label={`Run is ${run.status} — this view updates automatically…`} />
        </div>
      ) : digest ? (
        <Card>
          <Markdown>{digest.content}</Markdown>
        </Card>
      ) : (
        <div className={styles.empty}>No output was produced.</div>
      )}
    </section>
  );
}
