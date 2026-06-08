import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { getAgentDef, updateAgentDef } from "../api/endpoints";
import { useManifest } from "../api/useManifest";
import type { AgentDefinition } from "../api/types";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { ErrorBanner } from "../components/ErrorBanner";
import { Spinner } from "../components/Spinner";
import {
  ParamsEditor,
  SelectField,
  TextField,
  paramsToRows,
  rowsToParams,
} from "../synth/controls";
import type { ParamRow } from "../synth/controls";
import styles from "../workflows/Synth.module.css";

/** Structured editor for a custom agent def: prompt (+ builder/parser/model/params). */
export function AgentBuilder() {
  const { agentId = "" } = useParams();
  const manifest = useManifest();
  const navigate = useNavigate();

  const [def, setDef] = useState<AgentDefinition | null>(null);
  const [name, setName] = useState("");
  const [paramRows, setParamRows] = useState<ParamRow[]>([]);
  const [builtin, setBuiltin] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let on = true;
    void getAgentDef(agentId)
      .then((a) => {
        if (!on) return;
        setDef(a.definition);
        setName(a.name ?? "");
        setParamRows(paramsToRows(a.definition.params ?? {}));
        setBuiltin(a.builtin);
      })
      .catch((e) => setLoadError(e instanceof ApiError ? e.detail : "Failed to load agent."));
    return () => {
      on = false;
    };
  }, [agentId]);

  if (loadError) return <ErrorBanner message={loadError} />;
  if (!def || !manifest) {
    return (
      <div className={styles.center}>
        <Spinner label="Loading…" />
      </div>
    );
  }

  async function save() {
    if (!def) return;
    setSaveError(null);
    setSaved(false);
    setSaving(true);
    try {
      const definition = { ...def, params: rowsToParams(paramRows) };
      await updateAgentDef(agentId, { definition, name });
      setSaved(true);
    } catch (e) {
      setSaveError(e instanceof ApiError ? e.detail : "Failed to save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className={styles.builder}>
      <h1 className={styles.title}>Edit agent: {def.id}</h1>
      {builtin && (
        <ErrorBanner message="Built-in agents are read-only — clone one to edit." />
      )}
      {saveError && <ErrorBanner message={saveError} />}
      {saved && <p className={styles.muted}>Saved.</p>}

      <Card className={styles.section}>
        <div className={styles.grid}>
          <TextField label="name" value={name} onChange={setName} />
          <SelectField
            label="prompt_builder_ref"
            value={def.prompt_builder_ref}
            onChange={(v) => setDef({ ...def, prompt_builder_ref: v })}
            options={manifest.prompt_builders}
          />
          <SelectField
            label="parser_ref"
            value={def.parser_ref}
            onChange={(v) => setDef({ ...def, parser_ref: v })}
            options={manifest.parsers}
          />
          <TextField
            label="model (blank = inherit)"
            value={def.model ?? ""}
            onChange={(v) => setDef({ ...def, model: v || null })}
          />
        </div>
        <label className={styles.field}>
          <span className={styles.label}>
            system_prompt (templates may use {"{language}"} / {"{stance}"})
          </span>
          <textarea
            className={styles.textarea}
            value={def.system_prompt}
            onChange={(e) => setDef({ ...def, system_prompt: e.target.value })}
          />
        </label>
        <ParamsEditor rows={paramRows} onChange={setParamRows} />
      </Card>

      <div className={styles.actions}>
        <Button onClick={() => void save()} disabled={builtin || saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
        <Button variant="secondary" onClick={() => navigate("/agents")}>
          Back
        </Button>
      </div>
    </section>
  );
}
