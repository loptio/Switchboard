import { useState } from "react";

import { ApiError } from "../api/client";
import { resumeRun } from "../api/endpoints";
import type { CodingReviewPayload, ReviewPayload } from "../api/types";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ErrorBanner } from "../components/ErrorBanner";
import styles from "./RunDetail.module.css";

/** Coding diff-review body (Phase 10a): the agent's summary, the changed files, the
 *  status (a bounded `stopped_limit` stop is flagged prominently — hardening #3), and
 *  the unified diff. The human approves the diff or sends it back with feedback. */
export function CodingDiff({ coding }: { coding: CodingReviewPayload }) {
  const stopped = coding.status !== "completed";
  const tampered = coding.git_tampered ?? [];
  const commands = coding.commands ?? [];
  return (
    <div>
      {tampered.length > 0 && (
        <p role="alert" className={styles.limitBanner}>
          ⛔ The agent modified git internals (<code>{tampered.join(", ")}</code>) — a
          hook/config code-execution vector. It was reverted and this run will NOT be
          finalized. Reject and re-run.
        </p>
      )}
      {stopped && (
        <p role="alert" className={styles.limitBanner}>
          ⚠ The agent stopped at a limit/budget (<code>{coding.status}</code>) — this is
          partial work. Review the diff before approving.
        </p>
      )}
      {coding.task && (
        <p>
          <strong>Task:</strong> {coding.task}
        </p>
      )}
      <p>{coding.summary || "(no summary)"}</p>
      <strong>Changed files</strong>
      {coding.changed_files.length > 0 ? (
        <ul>
          {coding.changed_files.map((f, i) => (
            <li key={i}>
              <code>{f}</code>
            </li>
          ))}
        </ul>
      ) : (
        <p>
          <em>No files changed.</em>
        </p>
      )}
      {/* Phase 10b-2: the commands the agent ran — their side effects need not appear
          in the diff, so the reviewer sees them explicitly alongside it. */}
      <strong>Commands run</strong>
      {commands.length > 0 ? (
        <ul aria-label="commands">
          {commands.map((c, i) => (
            <li key={i}>
              <code>{c}</code>
            </li>
          ))}
        </ul>
      ) : (
        <p>
          <em>No commands run.</em>
        </p>
      )}
      <strong>Diff</strong>
      <pre className={styles.diff} aria-label="diff">
        {coding.diff || "(empty diff)"}
      </pre>
    </div>
  );
}

/** Human-review gate UI for an awaiting_input run: show the candidate + approve or
 *  send it back with feedback. The decision is handed to the worker via the API. */
export function ReviewPanel({
  runId,
  review,
  onResolved,
}: {
  runId: string;
  review: ReviewPayload | null;
  onResolved: () => void;
}) {
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function act(action: "approve" | "redo") {
    setError(null);
    setBusy(true);
    try {
      await resumeRun(runId, action, action === "redo" ? feedback : undefined);
      onResolved();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Failed to submit decision.");
    } finally {
      setBusy(false);
    }
  }

  const items = review?.digest?.items ?? [];
  const issues = review?.issues ?? [];
  const coding = review?.coding;

  return (
    <Card>
      <h2 className={styles.outputTitle}>Human review</h2>
      {error && <ErrorBanner message={error} />}
      <p>Review the candidate below, then approve it or send it back with feedback.</p>
      {coding ? (
        <CodingDiff coding={coding} />
      ) : (
        <>
          <ul>
            {items.map((it, i) => (
              <li key={i}>
                <a href={it.link} target="_blank" rel="noreferrer">
                  {it.title}
                </a>{" "}
                — {it.one_line_summary}
              </li>
            ))}
          </ul>
          {issues.length > 0 && (
            <div>
              <strong>Open issues</strong>
              <ul>
                {issues.map((iss, i) => (
                  <li key={i}>
                    [{iss.kind}] {iss.detail}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      <textarea
        aria-label="Feedback for a redo"
        placeholder="Feedback for a redo (optional)"
        value={feedback}
        onChange={(e) => setFeedback(e.target.value)}
        style={{ width: "100%", minHeight: "4rem", marginBottom: "0.5rem" }}
      />
      <div style={{ display: "flex", gap: "0.5rem" }}>
        <Button onClick={() => void act("approve")} disabled={busy}>
          Approve
        </Button>
        <Button variant="secondary" onClick={() => void act("redo")} disabled={busy}>
          Redo with feedback
        </Button>
      </div>
    </Card>
  );
}
