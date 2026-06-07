import styles from "./ui.module.css";

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className={styles.errorBanner} role="alert">
      {message}
    </div>
  );
}
