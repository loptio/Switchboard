import { ErrorBanner } from "../components/ErrorBanner";
import { Spinner } from "../components/Spinner";
import { ScheduleForm } from "./ScheduleForm";
import { ScheduleRow } from "./ScheduleRow";
import { useSchedules } from "./useSchedules";
import styles from "./Schedules.module.css";

export function SchedulesPage() {
  const { schedules, loading, error, refresh } = useSchedules();

  return (
    <section>
      <h1 className={styles.title}>Schedules</h1>

      <ScheduleForm onCreated={() => void refresh()} />

      {error && <ErrorBanner message={error} />}

      {loading ? (
        <div className={styles.center}>
          <Spinner label="Loading schedules…" />
        </div>
      ) : schedules.length === 0 ? (
        <div className={styles.empty}>No schedules yet. Create one above.</div>
      ) : (
        <div className={styles.list}>
          {schedules.map((schedule) => (
            <ScheduleRow
              key={schedule.id}
              schedule={schedule}
              onChanged={() => void refresh()}
            />
          ))}
        </div>
      )}
    </section>
  );
}
