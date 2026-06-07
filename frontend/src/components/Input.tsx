import type { InputHTMLAttributes } from "react";

import styles from "./ui.module.css";

interface Props extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
}

export function Input({ label, id, name, ...rest }: Props) {
  const inputId = id ?? name;
  return (
    <label className={styles.field} htmlFor={inputId}>
      <span className={styles.label}>{label}</span>
      <input className={styles.input} id={inputId} name={name} {...rest} />
    </label>
  );
}
