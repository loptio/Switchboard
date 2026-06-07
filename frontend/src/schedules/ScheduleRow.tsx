import { useState } from "react";

import { ApiError } from "../api/client";
import { deleteSchedule, updateSchedule } from "../api/endpoints";
import type { Schedule } from "../api/types";
import { Button } from "../components/Button";
import { ErrorBanner } from "../components/ErrorBanner";
import { Input } from "../components/Input";
import { formatTime } from "../lib/format";
import styles from "./Schedules.module.css";

/** One schedule: view with toggle/edit/delete, or an inline cron/tz editor. */
export function ScheduleRow({
  schedule,
  onChanged,
}: {
  schedule: Schedule;
  onChanged: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [cron, setCron] = useState(schedule.cron);
  const [tz, setTz] = useState(schedule.timezone);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>, fallback: string) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : fallback);
    } finally {
      setBusy(false);
    }
  }

  if (editing) {
    return (
      <div className={`${styles.row} ${styles.rowEditing}`}>
        {error && <ErrorBanner message={error} />}
        <div className={styles.editFields}>
          <Input label="Cron" name={`cron-${schedule.id}`} value={cron} onChange={(e) => setCron(e.target.value)} />
          <Input label="Timezone" name={`tz-${schedule.id}`} value={tz} onChange={(e) => setTz(e.target.value)} />
        </div>
        <div className={styles.actions}>
          <Button
            disabled={busy}
            onClick={() =>
              run(async () => {
                await updateSchedule(schedule.id, { cron, tz });
                setEditing(false);
              }, "Failed to save.")
            }
          >
            Save
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => setEditing(false)}>
            Cancel
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.row}>
      {error && <ErrorBanner message={error} />}
      <code className={styles.cron}>{schedule.cron}</code>
      <span className={styles.tz}>{schedule.timezone}</span>
      <span className={schedule.enabled ? styles.on : styles.off}>
        {schedule.enabled ? "Enabled" : "Disabled"}
      </span>
      <span className={styles.next}>next: {formatTime(schedule.next_run_at)}</span>
      <div className={styles.actions}>
        <Button
          variant="secondary"
          disabled={busy}
          onClick={() =>
            run(
              () => updateSchedule(schedule.id, { enabled: !schedule.enabled }),
              "Failed to update.",
            )
          }
        >
          {schedule.enabled ? "Disable" : "Enable"}
        </Button>
        <Button variant="secondary" disabled={busy} onClick={() => setEditing(true)}>
          Edit
        </Button>
        <Button
          variant="danger"
          disabled={busy}
          onClick={() => {
            if (window.confirm("Delete this schedule?")) {
              void run(() => deleteSchedule(schedule.id), "Failed to delete.");
            }
          }}
        >
          Delete
        </Button>
      </div>
    </div>
  );
}
