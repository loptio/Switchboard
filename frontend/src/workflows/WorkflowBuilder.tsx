import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { getWorkflowDef, triggerRun, updateWorkflowDef } from "../api/endpoints";
import { useManifest } from "../api/useManifest";
import type { Manifest, WfNode, WorkflowDefinition } from "../api/types";
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
import styles from "./Synth.module.css";

// --- node editor (recursive for fan_out bodies) ----------------------------

const HANDLER_KINDS = ["step", "human_review"];

function NodeEditor(props: {
  node: WfNode;
  manifest: Manifest;
  siblingIds: string[];
  inBody: boolean;
  onChange: (n: WfNode) => void;
  onRemove: () => void;
}) {
  const { node, manifest, siblingIds, inBody, onChange, onRemove } = props;
  const end = manifest.end;
  const targets = [...siblingIds.filter((id) => id !== node.id), end];
  const set = (patch: Partial<WfNode>) => onChange({ ...node, ...patch });

  return (
    <div className={styles.nodeCard}>
      <div className={styles.grid}>
        <TextField label="id" value={node.id} onChange={(v) => set({ id: v })} />
        <SelectField
          label="kind"
          value={node.kind}
          onChange={(v) => set({ kind: v as WfNode["kind"] })}
          options={Object.keys(manifest.node_kinds)}
        />
      </div>

      {HANDLER_KINDS.includes(node.kind) && (
        <div className={styles.grid}>
          <SelectField
            label="handler_ref"
            value={node.handler_ref ?? ""}
            onChange={(v) => set({ handler_ref: v })}
            options={manifest.node_handlers}
          />
          <SelectField
            label="agent_ref"
            value={node.agent_ref ?? ""}
            onChange={(v) => set({ agent_ref: v || undefined })}
            options={manifest.agents}
            allowEmpty
          />
          <TextField
            label="config_key"
            value={node.config_key ?? ""}
            onChange={(v) => set({ config_key: v || undefined })}
          />
        </div>
      )}

      {node.kind === "fan_out" && (
        <FanOutFields node={node} manifest={manifest} set={set} />
      )}

      {node.kind === "gather" && (
        <div className={styles.grid}>
          <SelectField
            label="compose_ref"
            value={node.compose_ref ?? ""}
            onChange={(v) => set({ compose_ref: v })}
            options={manifest.composers}
          />
          <TextField label="into" value={node.into ?? ""} onChange={(v) => set({ into: v })} />
        </div>
      )}

      {/* Edges only at the top level (body nodes run as a sequential map). */}
      {!inBody && (
        <EdgeEditor node={node} manifest={manifest} targets={targets} set={set} />
      )}

      <div>
        <Button variant="danger" onClick={onRemove}>
          Remove node
        </Button>
      </div>
    </div>
  );
}

function FanOutFields(props: {
  node: WfNode;
  manifest: Manifest;
  set: (patch: Partial<WfNode>) => void;
}) {
  const { node, manifest, set } = props;
  const body = node.body ?? [];
  const bodyIds = body.map((b) => b.id);
  return (
    <>
      <div className={styles.grid}>
        <TextField label="over" value={node.over ?? ""} onChange={(v) => set({ over: v })} />
        <TextField
          label="element_key"
          value={node.element_key ?? ""}
          onChange={(v) => set({ element_key: v })}
        />
        <TextField label="into" value={node.into ?? ""} onChange={(v) => set({ into: v })} />
        <SelectField
          label="collect_ref"
          value={node.collect_ref ?? ""}
          onChange={(v) => set({ collect_ref: v || undefined })}
          options={manifest.composers}
          allowEmpty
        />
      </div>
      <div className={styles.body}>
        <span className={styles.label}>body (runs in order)</span>
        {body.map((child, i) => (
          <NodeEditor
            key={i}
            node={child}
            manifest={manifest}
            siblingIds={bodyIds}
            inBody
            onChange={(n) => set({ body: body.map((b, j) => (j === i ? n : b)) })}
            onRemove={() => set({ body: body.filter((_, j) => j !== i) })}
          />
        ))}
        <div className={styles.actions}>
          <Button
            variant="secondary"
            onClick={() => set({ body: [...body, { id: `step${body.length + 1}`, kind: "step" }] })}
          >
            + body step
          </Button>
          <Button
            variant="secondary"
            onClick={() =>
              set({ body: [...body, { id: `fan${body.length + 1}`, kind: "fan_out", body: [] }] })
            }
          >
            + body fan_out
          </Button>
        </div>
      </div>
    </>
  );
}

function EdgeEditor(props: {
  node: WfNode;
  manifest: Manifest;
  targets: string[];
  set: (patch: Partial<WfNode>) => void;
}) {
  const { node, manifest, targets, set } = props;
  // fan_out / gather only use `next`.
  if (node.kind === "fan_out" || node.kind === "gather") {
    return (
      <SelectField
        label="next"
        value={node.next ?? ""}
        onChange={(v) => set({ next: v })}
        options={targets}
      />
    );
  }
  const mode = node.branch ? "branch" : "next";
  const routes = node.branch?.routes ?? {};
  const setRoute = (label: string, target: string) =>
    set({
      branch: {
        predicate_ref: node.branch?.predicate_ref ?? "",
        routes: { ...routes, [label]: target },
      },
    });
  return (
    <div className={styles.section}>
      <SelectField
        label="edge"
        value={mode}
        onChange={(v) =>
          v === "branch"
            ? set({ next: undefined, branch: { predicate_ref: "", routes: {} } })
            : set({ branch: undefined, next: targets[0] ?? manifest.end })
        }
        options={["next", "branch"]}
      />
      {mode === "next" ? (
        <SelectField
          label="next →"
          value={node.next ?? ""}
          onChange={(v) => set({ next: v })}
          options={targets}
        />
      ) : (
        <>
          <SelectField
            label="predicate_ref"
            value={node.branch?.predicate_ref ?? ""}
            onChange={(v) =>
              set({ branch: { predicate_ref: v, routes: node.branch?.routes ?? {} } })
            }
            options={manifest.predicates}
          />
          {Object.entries(routes).map(([label, target]) => (
            <div key={label} className={styles.rowKv}>
              <input className={styles.textInput} value={label} readOnly />
              <select
                className={styles.select}
                value={target}
                onChange={(e) => setRoute(label, e.target.value)}
              >
                {targets.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
              <Button
                variant="secondary"
                onClick={() => {
                  const { [label]: _drop, ...rest } = routes;
                  set({
                    branch: { predicate_ref: node.branch?.predicate_ref ?? "", routes: rest },
                  });
                }}
              >
                ✕
              </Button>
            </div>
          ))}
          <AddRoute onAdd={(label, target) => setRoute(label, target)} targets={targets} />
        </>
      )}
    </div>
  );
}

function AddRoute(props: { onAdd: (label: string, target: string) => void; targets: string[] }) {
  const [label, setLabel] = useState("");
  const [target, setTarget] = useState(props.targets[0] ?? "");
  return (
    <div className={styles.rowKv}>
      <input
        className={styles.textInput}
        placeholder="label"
        value={label}
        onChange={(e) => setLabel(e.target.value)}
      />
      <select className={styles.select} value={target} onChange={(e) => setTarget(e.target.value)}>
        {props.targets.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>
      <Button
        variant="secondary"
        onClick={() => {
          if (label) props.onAdd(label, target);
          setLabel("");
        }}
      >
        + route
      </Button>
    </div>
  );
}

// --- the builder page ------------------------------------------------------

export function WorkflowBuilder() {
  const { defId = "" } = useParams();
  const manifest = useManifest();
  const navigate = useNavigate();

  const [def, setDef] = useState<WorkflowDefinition | null>(null);
  const [name, setName] = useState("");
  const [paramRows, setParamRows] = useState<ParamRow[]>([]);
  const [builtin, setBuiltin] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let on = true;
    void getWorkflowDef(defId)
      .then((wf) => {
        if (!on) return;
        setDef(wf.definition);
        setName(wf.name ?? "");
        setParamRows(paramsToRows(wf.definition.params ?? {}));
        setBuiltin(wf.builtin);
      })
      .catch((e) =>
        setLoadError(e instanceof ApiError ? e.detail : "Failed to load workflow."),
      );
    return () => {
      on = false;
    };
  }, [defId]);

  if (loadError) return <ErrorBanner message={loadError} />;
  if (!def || !manifest) {
    return (
      <div className={styles.center}>
        <Spinner label="Loading…" />
      </div>
    );
  }

  const nodeIds = def.nodes.map((n) => n.id);
  const families = manifest.families.map((f) => f.id);

  function applyFamily(familyId: string) {
    const fam = manifest!.families.find((f) => f.id === familyId);
    if (fam && def) setDef({ ...def, source_ref: fam.source, output_ref: fam.output });
  }

  const currentFamily =
    manifest.families.find((f) => f.output === def.output_ref)?.id ?? "";

  async function save() {
    if (!def) return;
    setSaveError(null);
    setSaved(false);
    setSaving(true);
    try {
      const definition = { ...def, params: rowsToParams(paramRows) };
      await updateWorkflowDef(defId, { definition, name });
      setSaved(true);
    } catch (e) {
      setSaveError(e instanceof ApiError ? e.detail : "Failed to save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className={styles.builder}>
      <h1 className={styles.title}>Edit workflow: {def.id}</h1>
      {builtin && (
        <ErrorBanner message="Built-in workflows are read-only — clone one to edit." />
      )}
      {saveError && <ErrorBanner message={saveError} />}
      {saved && <p className={styles.muted}>Saved.</p>}

      <Card className={styles.section}>
        <div className={styles.grid}>
          <TextField label="name" value={name} onChange={setName} />
          <SelectField
            label="entry"
            value={def.entry}
            onChange={(v) => setDef({ ...def, entry: v })}
            options={nodeIds}
          />
          <SelectField
            label="family (source → output)"
            value={currentFamily}
            onChange={applyFamily}
            options={families}
          />
        </div>
        <ParamsEditor rows={paramRows} onChange={setParamRows} />
      </Card>

      <div className={styles.section}>
        <h3 className={styles.sectionTitle}>Nodes</h3>
        {def.nodes.map((node, i) => (
          <NodeEditor
            key={i}
            node={node}
            manifest={manifest}
            siblingIds={nodeIds}
            inBody={false}
            onChange={(n) =>
              setDef({ ...def, nodes: def.nodes.map((x, j) => (j === i ? n : x)) })
            }
            onRemove={() => setDef({ ...def, nodes: def.nodes.filter((_, j) => j !== i) })}
          />
        ))}
        <div className={styles.actions}>
          <Button
            variant="secondary"
            onClick={() =>
              setDef({
                ...def,
                nodes: [...def.nodes, { id: `node${def.nodes.length + 1}`, kind: "step" }],
              })
            }
          >
            + node
          </Button>
        </div>
      </div>

      <div className={styles.actions}>
        <Button onClick={() => void save()} disabled={builtin || saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
        <Button
          variant="secondary"
          onClick={async () => {
            const r = await triggerRun(defId);
            navigate(`/runs/${r.id}`);
          }}
        >
          Run now
        </Button>
        <Button variant="secondary" onClick={() => navigate("/workflows")}>
          Back
        </Button>
      </div>
    </section>
  );
}
