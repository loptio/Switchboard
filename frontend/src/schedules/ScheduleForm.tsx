import { useState } from "react";
import type { FormEvent } from "react";

import { ApiError } from "../api/client";
import { createSchedule } from "../api/endpoints";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ErrorBanner } from "../components/ErrorBanner";
import { Input } from "../components/Input";
import styles from "./Schedules.module.css";

/** Create-schedule form. Calls onCreated() so the page can refresh the list. */
export function ScheduleForm({ onCreated }: { onCreated: () => void }) {
  const [cron, setCron] = useState("0 6 * * *");
  const [tz, setTz] = useState("UTC");
  const [workflow, setWorkflow] = useState("news");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await createSchedule({ cron, tz, workflow });
      onCreated();
    } catch (err) {
      // The API returns 400 with a clear message for an invalid cron.
      setError(err instanceof ApiError ? err.detail : "Failed to create schedule.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className={styles.formCard}>
      <h2 className={styles.formTitle}>New schedule</h2>
      {error && <ErrorBanner message={error} />}
      <form onSubmit={onSubmit} className={styles.form}>
        <Input
          label="Cron (5 fields)"
          name="cron"
          value={cron}
          onChange={(e) => setCron(e.target.value)}
          placeholder="0 6 * * *"
          required
        />
        <Input
          label="Timezone"
          name="tz"
          value={tz}
          onChange={(e) => setTz(e.target.value)}
          placeholder="UTC"
          required
        />
        <Input
          label="Workflow"
          name="workflow"
          value={workflow}
          onChange={(e) => setWorkflow(e.target.value)}
          required
        />
        <Button type="submit" disabled={submitting}>
          {submitting ? "Creating…" : "Create"}
        </Button>
      </form>
    </Card>
  );
}
