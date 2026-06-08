import { useState } from "react";

import { ApiError } from "../api/client";
import { resumeRun } from "../api/endpoints";
import type { ReviewPayload } from "../api/types";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ErrorBanner } from "../components/ErrorBanner";
import styles from "./RunDetail.module.css";

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

  return (
    <Card>
      <h2 className={styles.outputTitle}>Human review</h2>
      {error && <ErrorBanner message={error} />}
      <p>Review the candidate below, then approve it or send it back with feedback.</p>
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
