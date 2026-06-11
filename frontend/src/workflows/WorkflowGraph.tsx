import type { WfNode, WorkflowDefinition } from "../api/types";
import styles from "./WorkflowGraph.module.css";

// A hand-rolled layered diagram of a WorkflowDefinition — no graph-layout
// dependency (these graphs are small: 3-8 top-level nodes). Nodes are placed in
// rows by their BFS depth from `entry`; edges are `next` (solid), `branch` routes
// (labeled), back-edges (loops, curved), and edges to the END sentinel (to a
// terminal node). fan_out bodies are shown inline inside the fan_out node.
//
// The same component renders a LIVE run: pass `statuses` (node id -> run state) and
// each node lights up (pending/running/done/failed). Pure presentational — the
// caller supplies the definition and, when monitoring, the per-node statuses.

const END = "__end__";
const NODE_W = 168;
const NODE_H = 64;
const V_GAP = 56;
const H_GAP = 28;
const PAD = 24;

export type NodeRunState = "pending" | "running" | "done" | "failed";

interface Placed {
  node: WfNode;
  x: number;
  y: number;
  depth: number;
}

/** The out-edges of a top-level node: each a (target, label?). branch routes carry
 *  their route key as the label; `next` is unlabeled. END targets are kept (drawn to
 *  the terminal). */
function outEdges(node: WfNode): { target: string; label?: string }[] {
  if (node.branch) {
    return Object.entries(node.branch.routes).map(([label, target]) => ({ target, label }));
  }
  if (node.next) return [{ target: node.next }];
  return [];
}

function layout(def: WorkflowDefinition) {
  const nodes = def.nodes;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const depth = new Map<string, number>();
  depth.set(def.entry, 0);
  const queue = [def.entry];
  let touchesEnd = false;
  while (queue.length) {
    const cur = queue.shift() as string;
    const node = byId.get(cur);
    if (!node) continue;
    for (const { target } of outEdges(node)) {
      if (target === END) {
        touchesEnd = true;
        continue;
      }
      if (!depth.has(target)) {
        depth.set(target, (depth.get(cur) ?? 0) + 1);
        queue.push(target);
      }
    }
  }
  // Any node unreachable from entry (shouldn't happen for a valid def) gets a row
  // after the deepest reached node, so it is still drawn.
  let maxDepth = 0;
  for (const d of depth.values()) maxDepth = Math.max(maxDepth, d);
  for (const n of nodes) {
    if (!depth.has(n.id)) {
      maxDepth += 1;
      depth.set(n.id, maxDepth);
    }
  }

  // Group node ids by depth, preserving definition order within a row.
  const rows = new Map<number, string[]>();
  for (const n of nodes) {
    const d = depth.get(n.id) as number;
    if (!rows.has(d)) rows.set(d, []);
    (rows.get(d) as string[]).push(n.id);
  }
  const endDepth = touchesEnd ? maxDepth + 1 : -1;

  const rowCount = Math.max(...[...rows.keys()], endDepth) + 1;
  const widest = Math.max(...[...rows.values()].map((r) => r.length), endDepth >= 0 ? 1 : 0);
  const contentW = widest * NODE_W + (widest - 1) * H_GAP;
  const width = contentW + PAD * 2;
  const height = rowCount * NODE_H + (rowCount - 1) * V_GAP + PAD * 2;

  function rowY(d: number) {
    return PAD + d * (NODE_H + V_GAP);
  }
  function placeRow(ids: string[], d: number): Placed[] {
    const rowW = ids.length * NODE_W + (ids.length - 1) * H_GAP;
    const startX = PAD + (contentW - rowW) / 2;
    return ids.map((id, i) => ({
      node: byId.get(id) as WfNode,
      x: startX + i * (NODE_W + H_GAP),
      y: rowY(d),
      depth: d,
    }));
  }

  const placed: Placed[] = [];
  for (const [d, ids] of rows) placed.push(...placeRow(ids, d));
  const placedById = new Map(placed.map((p) => [p.node.id, p]));

  // The END terminal (one box centered in its own row), if anything routes to it.
  const endPos =
    endDepth >= 0
      ? { x: PAD + (contentW - NODE_W) / 2, y: rowY(endDepth) }
      : null;

  return { placed, placedById, endPos, width, height };
}

function centerBottom(p: { x: number; y: number }) {
  return { x: p.x + NODE_W / 2, y: p.y + NODE_H };
}
function centerTop(p: { x: number; y: number }) {
  return { x: p.x + NODE_W / 2, y: p.y };
}

function nodeStateClass(s: NodeRunState | undefined): string {
  switch (s) {
    case "running":
      return styles.running;
    case "done":
      return styles.done;
    case "failed":
      return styles.failed;
    case "pending":
      return styles.pendingNode;
    default:
      return "";
  }
}

export function WorkflowGraph({
  definition,
  statuses,
}: {
  definition: WorkflowDefinition;
  statuses?: Record<string, NodeRunState>;
}) {
  const { placed, placedById, endPos, width, height } = layout(definition);

  // Build the edge list with geometry.
  const edges: {
    key: string;
    from: { x: number; y: number };
    to: { x: number; y: number };
    label?: string;
    back: boolean;
  }[] = [];
  for (const p of placed) {
    for (const { target, label } of outEdges(p.node)) {
      const fromPt = centerBottom(p);
      let toPt: { x: number; y: number };
      let back = false;
      if (target === END) {
        if (!endPos) continue;
        toPt = centerTop(endPos);
      } else {
        const tp = placedById.get(target);
        if (!tp) continue;
        toPt = centerTop(tp);
        back = tp.depth <= p.depth; // edge to same/earlier row = a loop
        if (back) {
          // leave from the right side, return to the target's right side
          edges.push({
            key: `${p.node.id}->${target}:${label ?? ""}`,
            from: { x: p.x + NODE_W, y: p.y + NODE_H / 2 },
            to: { x: tp.x + NODE_W, y: tp.y + NODE_H / 2 },
            label,
            back: true,
          });
          continue;
        }
      }
      edges.push({ key: `${p.node.id}->${target}:${label ?? ""}`, from: fromPt, to: toPt, label, back });
    }
  }

  return (
    <svg
      className={styles.graph}
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      role="img"
      aria-label="workflow graph"
    >
      <defs>
        <marker
          id="wfarrow"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="7"
          markerHeight="7"
          orient="auto-start-reverse"
        >
          <path d="M 0 0 L 10 5 L 0 10 z" className={styles.arrowHead} />
        </marker>
      </defs>

      {edges.map((e) => {
        if (e.back) {
          const bulge = Math.max(e.from.x, e.to.x) + 46;
          const midY = (e.from.y + e.to.y) / 2;
          const d = `M ${e.from.x} ${e.from.y} C ${bulge} ${e.from.y}, ${bulge} ${e.to.y}, ${e.to.x} ${e.to.y}`;
          return (
            <g key={e.key}>
              <path d={d} className={`${styles.edge} ${styles.loopEdge}`} markerEnd="url(#wfarrow)" />
              {e.label && (
                <text x={bulge + 4} y={midY} className={styles.loopLabel}>
                  ↩ {e.label}
                </text>
              )}
            </g>
          );
        }
        const midX = (e.from.x + e.to.x) / 2;
        const midY = (e.from.y + e.to.y) / 2;
        return (
          <g key={e.key}>
            <path
              d={`M ${e.from.x} ${e.from.y} L ${e.to.x} ${e.to.y}`}
              className={styles.edge}
              markerEnd="url(#wfarrow)"
            />
            {e.label && (
              <text x={midX} y={midY} className={styles.edgeLabel}>
                {e.label}
              </text>
            )}
          </g>
        );
      })}

      {placed.map((p) => {
        const n = p.node;
        const agent = n.agent_ref ? `agent: ${n.agent_ref}` : null;
        const detail =
          n.kind === "fan_out"
            ? `over ${n.over} → ${(n.body ?? []).map((b) => b.id).join(", ")}`
            : n.kind === "gather"
              ? `compose: ${n.compose_ref}`
              : agent;
        return (
          <g key={n.id} transform={`translate(${p.x}, ${p.y})`}>
            <rect
              width={NODE_W}
              height={NODE_H}
              rx={8}
              className={`${styles.node} ${styles[`kind_${n.kind}`] ?? ""} ${nodeStateClass(
                statuses?.[n.id],
              )}`}
            />
            <text x={10} y={20} className={styles.nodeId}>
              {n.id}
            </text>
            <text x={10} y={37} className={styles.nodeKind}>
              {n.kind}
              {n.id === definition.entry ? " · entry" : ""}
            </text>
            {detail && (
              <text x={10} y={53} className={styles.nodeDetail}>
                {detail.length > 24 ? detail.slice(0, 23) + "…" : detail}
              </text>
            )}
            {statuses?.[n.id] === "running" && <circle cx={NODE_W - 12} cy={12} r={5} className={styles.pulse} />}
          </g>
        );
      })}

      {endPos && (
        <g transform={`translate(${endPos.x}, ${endPos.y})`}>
          <rect width={NODE_W} height={NODE_H} rx={32} className={`${styles.node} ${styles.endNode}`} />
          <text x={NODE_W / 2} y={NODE_H / 2 + 5} className={styles.endLabel}>
            END
          </text>
        </g>
      )}
    </svg>
  );
}
