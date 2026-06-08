import type { RunStatus } from "../api/types";
import styles from "./RunStatusBadge.module.css";

const LABEL: Record<RunStatus, string> = {
  pending: "Pending",
  running: "Running",
  success: "Success",
  failed: "Failed",
  awaiting_input: "Awaiting input",
};

/** A color-coded badge so a run's status reads at a glance. */
export function RunStatusBadge({ status }: { status: RunStatus }) {
  return <span className={`${styles.badge} ${styles[status]}`}>{LABEL[status]}</span>;
}
