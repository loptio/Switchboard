// Shared structured-form controls for the synthesizer (no raw JSON, decision B).
import { Button } from "../components/Button";
import styles from "../workflows/Synth.module.css";

export function TextField(props: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className={styles.field}>
      <span className={styles.label}>{props.label}</span>
      <input
        className={styles.textInput}
        value={props.value}
        placeholder={props.placeholder}
        onChange={(e) => props.onChange(e.target.value)}
      />
    </label>
  );
}

export function SelectField(props: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
  allowEmpty?: boolean;
}) {
  return (
    <label className={styles.field}>
      <span className={styles.label}>{props.label}</span>
      <select
        className={styles.select}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
      >
        {props.allowEmpty && <option value="">— none —</option>}
        {props.options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
}

export interface ParamRow {
  key: string;
  value: string;
}

export function paramsToRows(params: Record<string, unknown>): ParamRow[] {
  return Object.entries(params).map(([key, v]) => ({
    key,
    value: typeof v === "string" ? v : JSON.stringify(v),
  }));
}

export function rowsToParams(rows: ParamRow[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const { key, value } of rows) {
    if (!key) continue;
    try {
      out[key] = JSON.parse(value); // numbers / lists / objects
    } catch {
      out[key] = value; // a plain string (e.g. 简体中文)
    }
  }
  return out;
}

export function ParamsEditor(props: {
  rows: ParamRow[];
  onChange: (rows: ParamRow[]) => void;
}) {
  const { rows, onChange } = props;
  return (
    <div className={styles.section}>
      <h3 className={styles.sectionTitle}>Params</h3>
      {rows.map((row, i) => (
        <div key={i} className={styles.rowKv}>
          <input
            className={styles.textInput}
            placeholder="name"
            value={row.key}
            onChange={(e) =>
              onChange(rows.map((r, j) => (j === i ? { ...r, key: e.target.value } : r)))
            }
          />
          <input
            className={styles.textInput}
            placeholder='value (e.g. 2 or ["a","b"])'
            value={row.value}
            onChange={(e) =>
              onChange(rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))
            }
          />
          <Button variant="secondary" onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            ✕
          </Button>
        </div>
      ))}
      <div>
        <Button variant="secondary" onClick={() => onChange([...rows, { key: "", value: "" }])}>
          + param
        </Button>
      </div>
    </div>
  );
}
