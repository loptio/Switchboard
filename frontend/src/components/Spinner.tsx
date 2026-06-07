import styles from "./ui.module.css";

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <span className={styles.spinner} role="status" aria-live="polite">
      {label}
    </span>
  );
}
