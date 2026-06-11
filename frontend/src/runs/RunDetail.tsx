import { Link, useParams } from "react-router-dom";

import type { CodingReviewPayload } from "../api/types";
import { Card } from "../components/Card";
import { ErrorBanner } from "../components/ErrorBanner";
import { Markdown } from "../components/Markdown";
import { Spinner } from "../components/Spinner";
import { formatTime } from "../lib/format";
import { WorkflowGraph } from "../workflows/WorkflowGraph";
import { CodingDiff, ReviewPanel } from "./ReviewPanel";
import { RunStatusBadge } from "./RunStatusBadge";
import { useRun } from "./useRun";
import styles from "./RunDetail.module.css";

// Phase 11 observability: human-friendly labels for the run's quality verdict +
// email delivery status (RunOut.meta).
const VERDICT_LABEL: Record<string, string> = {
  passed: "✓ Passed review",
  accepted_at_cap: "⚠ Accepted with open issues",
  inconclusive: "⚠ Verification inconclusive",
  human_approved: "✓ Human-approved",
  "reviewer:approved": "✓ Auto-reviewer approved",
  "reviewer:not_converged": "⚠ Auto-reviewer: not converged",
};
const EMAIL_LABEL: Record<string, string> = {
  sent: "✓ Sent",
  skipped: "— Not configured",
  failed: "✕ Delivery failed",
};

export function RunDetail() {
  const { id = "" } = useParams();
  const { run, outputs, review, definition, nodeStatuses, loading, error, notFound, reload } =
    useRun(id);

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

  // A coding run's deliverable carries its diff + the SHELL COMMANDS it ran (Phase 10b-2)
  // and a .git-tamper flag in `data` — render the same audit view as the review gate so
  // the commands (whose side effects are not in the diff) are visible on a finished run.
  const coding = outputs.find((o) => o.type === "coding");
  const digest = outputs.find((o) => o.type === "digest") ?? outputs[0];
  const inProgress = run.status === "pending" || run.status === "running";
  // "Live" includes awaiting_input — the run is paused at a gate, still active, and
  // the graph keeps polling and lighting up nodes.
  const live = inProgress || run.status === "awaiting_input";

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
        {run.meta?.verdict && (
          <div>
            <dt>Quality</dt>
            <dd>{VERDICT_LABEL[run.meta.verdict] ?? run.meta.verdict}</dd>
          </div>
        )}
        {run.meta?.email && (
          <div>
            <dt>Email</dt>
            <dd>{EMAIL_LABEL[run.meta.email] ?? run.meta.email}</dd>
          </div>
        )}
        {run.meta?.commit && (
          <div>
            <dt>Commit</dt>
            <dd>
              <code>{run.meta.commit}</code>
            </dd>
          </div>
        )}
      </dl>

      {run.error && <ErrorBanner message={run.error} />}

      {definition && (
        <>
          <h2 className={styles.outputTitle}>
            Workflow{" "}
            {live && (
              <span className={styles.liveTag} role="status">
                ● live
              </span>
            )}
          </h2>
          <Card>
            <WorkflowGraph definition={definition} statuses={nodeStatuses} />
          </Card>
        </>
      )}

      {run.status === "awaiting_input" ? (
        <ReviewPanel runId={id} review={review} onResolved={() => void reload()} />
      ) : (
        <>
          <h2 className={styles.outputTitle}>Output</h2>
          {inProgress ? (
            <div className={styles.pending}>
              <Spinner label={`Run is ${run.status} — this view updates automatically…`} />
            </div>
          ) : coding?.data ? (
            <Card>
              <CodingDiff coding={coding.data as unknown as CodingReviewPayload} />
            </Card>
          ) : digest ? (
            <Card>
              <Markdown>{digest.content}</Markdown>
            </Card>
          ) : (
            <div className={styles.empty}>No output was produced.</div>
          )}
        </>
      )}
    </section>
  );
}
