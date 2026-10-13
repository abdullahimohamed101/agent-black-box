import type { RunOut } from "@/lib/api/types";
import { formatDuration, formatOffset } from "./format";
import type { DerivedSpan } from "./spans";
import { describe } from "./timeline";
import { contentText } from "./withheld";

/**
 * Pure waterfall logic (spec §97, plan D6-D8): the span tree under a synthetic run root, lanes, the time scale, the
 * critical path and the text shown for a span. No React here. Every walk is iterative: a stored trace can hold a
 * 10,000-deep chain, a cycle (the API accepts one) or thousands of roots, and none of them may overflow the stack,
 * drop a span or throw.
 *
 * Text shown to a person (labels, aggregate rows, tooltips) is built from `describe` and `contentText` only, never from
 * raw attributes, so content withheld from the viewer's role stays withheld here too (ADR-061).
 */

/** Id of the synthetic root node (span ids start with `spn_`, so they cannot collide with it). */
export const ROOT_ID = "run";
/** Rows deeper than this are drawn at this indent; the row says its real level. */
export const MAX_INDENT = 32;
/** More spans than this and everything below `AUTO_COLLAPSE_DEPTH` starts collapsed. */
export const AUTO_COLLAPSE_SPANS = 2000;
export const AUTO_COLLAPSE_DEPTH = 2;
export const MAX_ZOOM = 4096;
const LABEL_MAX = 80;
const AGENT_MAX = 64;

export type RunBounds = {
  startMs: number;
  endMs: number;
  /**
   * Replay (Phase 12): where spans that have opened and not closed stop, instead of at the run's end. The axis keeps the
   * run's full extent (`endMs`), so the bars grow along a fixed scale as the playhead moves.
   */
  openEndMs?: number;
};

export type NodeFlags = {
  /** Its parent span never arrived. */
  orphan: boolean;
  /** The parent chain looped; the loop was cut here. */
  cycle: boolean;
  /** Opened and never closed: drawn open-ended, ending at the run's end. */
  unfinished: boolean;
  /** Its timestamps fall outside its parent's: another emitter's clock. */
  skewed: boolean;
};

export type Node = {
  id: string;
  /** Null for the synthetic run root. */
  span: DerivedSpan | null;
  parentId: string | null;
  children: string[];
  /** 0 for the root, 1 for top-level spans. */
  depth: number;
  startMs: number;
  endMs: number;
  /** The interval clamped into the parent's, which the critical-path walk uses. */
  clampStartMs: number;
  clampEndMs: number;
  flags: NodeFlags;
  /** Spans strictly below this node. */
  descendants: number;
  subtreeStartMs: number;
  subtreeEndMs: number;
  subtreeError: boolean;
};

export type Tree = {
  nodes: Map<string, Node>;
  root: Node;
  /** Every node id, parents before children, siblings by start time. */
  order: string[];
  spanCount: number;
};

const ms = (iso: string | null | undefined, fallback: number): number => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? t : fallback;
};

/** The run's own extent: its start, and its completion or (while running) its last event. */
export function runBounds(
  run: Pick<RunOut, "started_at" | "completed_at" | "summary">,
  fallbackMs: number = 0,
): RunBounds {
  const startMs = ms(run.started_at, fallbackMs);
  const last = run.summary["last_event_at"];
  const endMs = ms(run.completed_at ?? (typeof last === "string" ? last : null), startMs);
  return { startMs, endMs: Math.max(startMs, endMs) };
}

/** When an unfinished span stops, for layout and for the critical-path walk. */
export const spanEnd = (span: DerivedSpan, runEndMs: number): number => {
  const start = ms(span.startedAt ?? span.firstAt, runEndMs);
  if (span.endedAt) return Math.max(start, ms(span.endedAt, start));
  if (span.startedAt) return Math.max(start, runEndMs); // an opener with no closer
  return Math.max(start, ms(span.lastAt, start));
};

/** Build the tree. Never throws and never drops a span, whatever the parent pointers say. */
export function buildTree(spans: readonly DerivedSpan[], bounds: RunBounds): Tree {
  const byId = new Map<string, DerivedSpan>();
  for (const s of spans) if (!byId.has(s.spanId)) byId.set(s.spanId, s);

  // Where the run ends: its own end, or the latest thing we saw (a live run's page can be ahead of its summary).
  let runEnd = bounds.endMs;
  let runStart = bounds.startMs;
  for (const s of byId.values()) {
    runEnd = Math.max(runEnd, ms(s.lastAt, runEnd), ms(s.endedAt, runEnd));
    runStart = Math.min(runStart, ms(s.startedAt ?? s.firstAt, runStart));
  }

  // Cycles: walk each parent chain once with a three-state mark; a chain that returns to itself is cut at its smallest id.
  const cutAt = new Set<string>();
  const state = new Map<string, 1 | 2>();
  const parentOf = (id: string): string | null => {
    const p = byId.get(id)?.parentSpanId ?? null;
    return p !== null && byId.has(p) ? p : null;
  };
  for (const start of byId.keys()) {
    if (state.has(start)) continue;
    const path: string[] = [];
    let cur: string | null = start;
    while (cur !== null && !state.has(cur)) {
      state.set(cur, 1);
      path.push(cur);
      cur = parentOf(cur);
    }
    if (cur !== null && state.get(cur) === 1) {
      const members = path.slice(path.indexOf(cur));
      cutAt.add(members.reduce((a, b) => (b < a ? b : a)));
    }
    for (const id of path) state.set(id, 2);
  }

  const nodes = new Map<string, Node>();
  const blank = (id: string, span: DerivedSpan | null, start: number, end: number): Node => ({
    id,
    span,
    parentId: null,
    children: [],
    depth: 0,
    startMs: start,
    endMs: end,
    clampStartMs: start,
    clampEndMs: end,
    flags: { orphan: false, cycle: false, unfinished: false, skewed: false },
    descendants: 0,
    subtreeStartMs: start,
    subtreeEndMs: end,
    subtreeError: false,
  });
  const root = blank(ROOT_ID, null, runStart, runEnd);
  nodes.set(ROOT_ID, root);
  for (const [id, s] of byId) {
    const start = ms(s.startedAt ?? s.firstAt, runStart);
    const n = blank(
      id,
      s,
      start,
      spanEnd(s, bounds.openEndMs === undefined ? runEnd : Math.min(runEnd, bounds.openEndMs)),
    );
    n.flags.unfinished = s.startedAt !== null && s.endedAt === null;
    n.flags.cycle = cutAt.has(id);
    nodes.set(id, n);
  }

  // Attach: a span with no (usable) parent hangs under the run root.
  for (const [id, s] of byId) {
    const n = nodes.get(id)!;
    const declared = s.parentSpanId;
    const parent = cutAt.has(id) ? null : parentOf(id);
    if (declared !== null && !byId.has(declared) && declared !== id) n.flags.orphan = true;
    n.parentId = parent ?? ROOT_ID;
    nodes.get(n.parentId)!.children.push(id);
  }
  const byStart = (a: string, b: string): number => {
    const d = nodes.get(a)!.startMs - nodes.get(b)!.startMs;
    return d !== 0 ? d : a < b ? -1 : a > b ? 1 : 0;
  };
  for (const n of nodes.values()) n.children.sort(byStart);

  // Preorder, iteratively: depth, clamped intervals and skew follow the parent's, so parents come first.
  const order: string[] = [];
  const stack: string[] = [ROOT_ID];
  while (stack.length > 0) {
    const id = stack.pop()!;
    const n = nodes.get(id)!;
    order.push(id);
    if (id !== ROOT_ID) {
      const p = nodes.get(n.parentId!)!;
      n.depth = p.depth + 1;
      const cs = Math.min(Math.max(n.startMs, p.clampStartMs), p.clampEndMs);
      n.clampStartMs = cs;
      n.clampEndMs = Math.max(Math.min(n.endMs, p.clampEndMs), cs);
      if (p.id !== ROOT_ID) {
        n.flags.skewed =
          n.startMs < p.startMs ||
          (!n.flags.unfinished && !p.flags.unfinished && n.endMs > p.endMs);
      }
    }
    for (let i = n.children.length - 1; i >= 0; i--) stack.push(n.children[i]!);
  }

  // Subtree aggregates, bottom-up (reverse preorder visits children before their parent).
  for (let i = order.length - 1; i >= 0; i--) {
    const n = nodes.get(order[i]!)!;
    n.subtreeError = n.span?.hasError ?? false;
    n.subtreeStartMs = n.startMs;
    n.subtreeEndMs = n.endMs;
    for (const c of n.children) {
      const child = nodes.get(c)!;
      n.descendants += child.descendants + 1;
      n.subtreeStartMs = Math.min(n.subtreeStartMs, child.subtreeStartMs);
      n.subtreeEndMs = Math.max(n.subtreeEndMs, child.subtreeEndMs);
      n.subtreeError ||= child.subtreeError;
    }
  }
  return { nodes, root, order, spanCount: byId.size };
}

export type Domain = { startMs: number; endMs: number };

/** The whole time range the tree covers (at least 1 ms wide, so a scale never divides by zero). */
export function timeDomain(tree: Tree): Domain {
  const startMs = Math.min(tree.root.startMs, tree.root.subtreeStartMs);
  const endMs = Math.max(tree.root.endMs, tree.root.subtreeEndMs);
  return { startMs, endMs: Math.max(endMs, startMs + 1) };
}

// ---- rows ---------------------------------------------------------------------------------------------------------

export type Aggregate = {
  /** Spans in the subtree, including its root. */
  count: number;
  startMs: number;
  endMs: number;
  hasError: boolean;
};

export type SpanRow = {
  kind: "span";
  key: string;
  node: Node;
  /** Indent steps drawn (capped at MAX_INDENT). */
  indent: number;
  /** The real nesting level, 1-based, for aria-level and the chip of a deep row. */
  level: number;
  hasChildren: boolean;
  expanded: boolean;
};
export type AggregateRow = {
  kind: "aggregate";
  key: string;
  node: Node;
  indent: number;
  level: number;
  aggregate: Aggregate;
};
export type LaneRow = {
  kind: "lane";
  key: string;
  agentId: string;
  /** The agent that delegated to this one, when the lane's first span nests under another agent's. */
  delegatedBy: string | null;
  count: number;
  startMs: number;
  endMs: number;
  hasError: boolean;
};
export type WaterfallRow = SpanRow | AggregateRow | LaneRow;

export const aggregate = (node: Node): Aggregate => ({
  count: node.descendants + 1,
  startMs: node.subtreeStartMs,
  endMs: node.subtreeEndMs,
  hasError: node.subtreeError,
});

/** Whether the tree starts collapsed below depth 2 (only very large runs). */
export const autoCollapseDepth = (tree: Tree): number | null =>
  tree.spanCount > AUTO_COLLAPSE_SPANS ? AUTO_COLLAPSE_DEPTH : null;

/**
 * Rows of the tree view in reading order. `toggled` holds the nodes whose state the reader flipped away from the
 * default (expanded, or collapsed at `autoDepth` and deeper). A collapsed node is one aggregate row for its whole subtree.
 */
export function treeRows(
  tree: Tree,
  toggled: ReadonlySet<string>,
  autoDepth: number | null,
): WaterfallRow[] {
  const rows: WaterfallRow[] = [];
  const stack: string[] = [ROOT_ID];
  while (stack.length > 0) {
    const n = tree.nodes.get(stack.pop()!)!;
    const hasChildren = n.children.length > 0;
    const byDefault = autoDepth !== null && n.id !== ROOT_ID && n.depth >= autoDepth;
    const collapsed = hasChildren && byDefault !== toggled.has(n.id);
    const level = n.depth + 1;
    const indent = Math.min(n.depth, MAX_INDENT);
    if (collapsed) {
      rows.push({ kind: "aggregate", key: n.id, node: n, indent, level, aggregate: aggregate(n) });
      continue;
    }
    rows.push({
      kind: "span",
      key: n.id,
      node: n,
      indent,
      level,
      hasChildren,
      expanded: hasChildren,
    });
    for (let i = n.children.length - 1; i >= 0; i--) stack.push(n.children[i]!);
  }
  return rows;
}

/** Rows of the lanes view: one header per agent (lanes ordered by first start), then that agent's spans in tree order. */
export function laneRows(tree: Tree): WaterfallRow[] {
  const lanes = new Map<string, Node[]>();
  for (const id of tree.order) {
    const n = tree.nodes.get(id)!;
    if (!n.span) continue;
    const list = lanes.get(n.span.agentId);
    if (list) list.push(n);
    else lanes.set(n.span.agentId, [n]);
  }
  const first = (list: Node[]) => list.reduce((m, n) => Math.min(m, n.startMs), Infinity);
  const ordered = [...lanes.entries()].sort(
    ([a, la], [b, lb]) => first(la) - first(lb) || (a < b ? -1 : a > b ? 1 : 0),
  );
  const rows: WaterfallRow[] = [];
  for (const [agentId, list] of ordered) {
    const earliest = list.reduce((m, n) => (n.startMs < m.startMs ? n : m));
    const parent = earliest.parentId ? tree.nodes.get(earliest.parentId) : undefined;
    const delegatedBy =
      parent?.span && parent.span.agentId !== agentId ? parent.span.agentId : null;
    rows.push({
      kind: "lane",
      key: `lane:${agentId}`,
      agentId,
      delegatedBy,
      count: list.length,
      startMs: first(list),
      endMs: list.reduce((m, n) => Math.max(m, n.endMs), -Infinity),
      hasError: list.some((n) => n.span?.hasError),
    });
    for (const n of list)
      rows.push({
        kind: "span",
        key: n.id,
        node: n,
        indent: 0,
        level: 2,
        hasChildren: false,
        expanded: false,
      });
  }
  return rows;
}

/** Agents in lane order (first start), for colour assignment that stays put as spans arrive. */
export function agentOrder(tree: Tree): string[] {
  return laneRows(tree)
    .filter((r): r is LaneRow => r.kind === "lane")
    .map((r) => r.agentId);
}

// ---- time scale ---------------------------------------------------------------------------------------------------

export type View = { zoom: number; panMs: number };
export const FIT: View = { zoom: 1, panMs: 0 };

const clamp = (v: number, lo: number, hi: number): number => Math.min(Math.max(v, lo), hi);

/** The visible time window of a view: `zoom` times narrower than the domain, panned by `panMs` from its start. */
export function viewport(domain: Domain, view: View): Domain {
  const total = domain.endMs - domain.startMs;
  const zoom = clamp(Number.isFinite(view.zoom) ? view.zoom : 1, 1, MAX_ZOOM);
  const visible = total / zoom;
  const start =
    domain.startMs + clamp(Number.isFinite(view.panMs) ? view.panMs : 0, 0, total - visible);
  return { startMs: start, endMs: start + visible };
}

/** Time to a position in `[0, width]` for the view; `w` converts a duration. Pass 100 for percentages. */
export function scale(domain: Domain, zoom: number, panMs: number, width: number) {
  const vp = viewport(domain, { zoom, panMs });
  const span = vp.endMs - vp.startMs;
  return {
    startMs: vp.startMs,
    endMs: vp.endMs,
    x: (t: number) => ((t - vp.startMs) / span) * width,
    w: (d: number) => (d / span) * width,
    invert: (x: number) => vp.startMs + (x / width) * span,
  };
}

/** Zoom by `factor` keeping the time under `anchor` (0..1 across the window) where it is. */
export function zoomView(domain: Domain, view: View, factor: number, anchor = 0.5): View {
  const before = viewport(domain, view);
  const zoom = clamp(view.zoom * factor, 1, MAX_ZOOM);
  const total = domain.endMs - domain.startMs;
  const visible = total / zoom;
  const focus = before.startMs + (before.endMs - before.startMs) * anchor;
  const start = focus - visible * anchor;
  return { zoom, panMs: clamp(start - domain.startMs, 0, total - visible) };
}

/** Pan by a fraction of the visible window (negative = earlier). */
export function panView(domain: Domain, view: View, fraction: number): View {
  const vp = viewport(domain, view);
  const total = domain.endMs - domain.startMs;
  const visible = vp.endMs - vp.startMs;
  return {
    zoom: view.zoom,
    panMs: clamp(vp.startMs - domain.startMs + visible * fraction, 0, total - visible),
  };
}

const STEPS = [1, 2, 5];
export type Tick = { ms: number; label: string };

/** About `count` round tick marks across a window, labelled as offsets from the domain start. */
export function ticks(domain: Domain, window: Domain, count = 6): Tick[] {
  const span = window.endMs - window.startMs;
  if (!(span > 0)) return [];
  const raw = span / count;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = (STEPS.map((s) => s * pow).find((s) => s >= raw) ?? 10 * pow) || 1;
  const out: Tick[] = [];
  for (let t = Math.ceil((window.startMs - domain.startMs) / step) * step; ; t += step) {
    const at = domain.startMs + t;
    if (at > window.endMs || out.length > count * 3) break;
    out.push({ ms: at, label: formatOffset(t) });
  }
  return out;
}

// ---- critical path ------------------------------------------------------------------------------------------------

export type Segment = { spanId: string; startMs: number; endMs: number };
export type CriticalPath = { spanIds: Set<string>; segments: Segment[] };

/**
 * Jaeger's last-finishing-child walk. From the run's end, take the child that finishes last at or before the current
 * point, put the stretch after it on the parent, descend into it, and continue in the parent from the child's start.
 * Children are clamped into their parent first, so a skewed child cannot pull the path outside it. Iterative.
 */
export function criticalPath(tree: Tree): CriticalPath {
  const spanIds = new Set<string>();
  const segments: Segment[] = [];
  type Frame = { node: Node; cur: number; kids: Node[]; i: number };
  const open = (node: Node, cur: number): Frame => ({
    node,
    cur,
    i: 0,
    kids: node.children
      .map((c) => tree.nodes.get(c)!)
      .sort(
        (a, b) =>
          b.clampEndMs - a.clampEndMs ||
          b.clampStartMs - a.clampStartMs ||
          (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
      ),
  });
  const stack: Frame[] = [open(tree.root, tree.root.clampEndMs)];
  while (stack.length > 0) {
    const f = stack[stack.length - 1]!;
    while (f.i < f.kids.length && f.kids[f.i]!.clampEndMs > f.cur) f.i++;
    const child = f.kids[f.i];
    if (!child) {
      if (f.node.id !== ROOT_ID && f.cur > f.node.clampStartMs)
        segments.push({ spanId: f.node.id, startMs: f.node.clampStartMs, endMs: f.cur });
      stack.pop();
      continue;
    }
    f.i++;
    if (f.node.id !== ROOT_ID && f.cur > child.clampEndMs)
      segments.push({ spanId: f.node.id, startMs: child.clampEndMs, endMs: f.cur });
    f.cur = child.clampStartMs;
    spanIds.add(child.id);
    stack.push(open(child, child.clampEndMs));
  }
  return { spanIds, segments };
}

// ---- text (the only place a person's eyes get span content) ---------------------------------------------------------

export const clip = (text: string, max: number): string =>
  text.length > max ? `${text.slice(0, max - 1)}…` : text;

/** An agent id for a chip: text, at most 64 characters. */
export const agentChip = (agentId: string): string => clip(agentId, AGENT_MAX);

/**
 * What a span is called. Built from `describe` and `contentText`: a command or path the viewer's role may not see comes
 * out as the withheld notice. Span names and agent ids are labels by contract (KI-078): shown as text, never interpreted.
 */
export function spanLabel(span: DerivedSpan): string {
  const head = span.head;
  const named = contentText(head, "span.name");
  if (span.kind === "agent") return clip(named ?? span.agentId, LABEL_MAX);
  const described = describe(head);
  return clip(
    described || named || head.event_type.replace(/\.(started|completed|failed)$/, ""),
    LABEL_MAX,
  );
}

export const nodeLabel = (node: Node): string => (node.span ? spanLabel(node.span) : "Run");

export type Field = { label: string; value: string };

/** The tooltip's rows. Text from `spanLabel` and metadata only; nothing is read from raw attributes. */
export function tooltipFields(node: Node, domainStartMs: number): Field[] {
  const duration = node.endMs - node.startMs;
  const fields: Field[] = [{ label: "Span", value: nodeLabel(node) }];
  const span = node.span;
  if (span) {
    fields.push({ label: "Agent", value: agentChip(span.agentId) });
    fields.push({ label: "Kind", value: span.kind ?? "event" });
    fields.push({
      label: "Status",
      value: span.status ?? (node.flags.unfinished ? "no end recorded" : "—"),
    });
    fields.push({ label: "Events", value: String(span.eventCount) });
  }
  fields.push({ label: "Start", value: formatOffset(node.startMs - domainStartMs) });
  fields.push({
    label: "Duration",
    value: formatDuration(span?.durationMs ?? (node.flags.unfinished ? null : duration)),
  });
  const flags = (Object.entries(node.flags) as [keyof NodeFlags, boolean][])
    .filter(([, on]) => on)
    .map(([k]) => FLAG_TEXT[k]);
  if (flags.length > 0) fields.push({ label: "Notes", value: flags.join(", ") });
  return fields;
}

export const FLAG_TEXT: Record<keyof NodeFlags, string> = {
  orphan: "parent not seen",
  cycle: "parent loop cut here",
  unfinished: "no end recorded",
  skewed: "clock skew",
};

const KINDS = new Set(["agent", "llm", "tool", "shell", "db", "custom"]);

/** The class that colours a bar. Status wins (errors and timeouts), else the span kind; text always repeats it. */
export function colourClass(kind: string | null, status: string | null): string {
  if (status === "error") return "wf-error";
  if (status === "timeout") return "wf-timeout";
  return `wf-kind-${kind && KINDS.has(kind) ? kind : "other"}`;
}
