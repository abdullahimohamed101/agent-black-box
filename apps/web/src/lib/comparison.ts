import type { ComparisonOut, RowOut, StepOut } from "./api/types";
import { DASH, formatCost, formatDuration, formatInt } from "./format";

/**
 * Presentation rules for the compare page. What a reader may see is decided by the API (it computes the whole
 * comparison from the reader's projection and says so in `alignment`); nothing here looks at a role.
 */

type MetricMeta = { label: string; format: (n: number) => string; lowerIsBetter: boolean };

const count = (n: number) => formatInt(n);
export const METRICS: Record<string, MetricMeta> = {
  duration_ms: { label: "Duration", format: formatDuration, lowerIsBetter: true },
  estimated_cost_usd: { label: "Cost", format: formatCost, lowerIsBetter: true },
  retry_cost_usd: { label: "Retry cost", format: formatCost, lowerIsBetter: true },
  llm_calls: { label: "Model calls", format: count, lowerIsBetter: false },
  tool_calls: { label: "Tool calls", format: count, lowerIsBetter: false },
  retry_count: { label: "Retries", format: count, lowerIsBetter: true },
  error_count: { label: "Errors", format: count, lowerIsBetter: true },
  files_modified: { label: "Files modified", format: count, lowerIsBetter: false },
  input_tokens: { label: "Input tokens", format: count, lowerIsBetter: false },
  output_tokens: { label: "Output tokens", format: count, lowerIsBetter: false },
};

export type Direction = "up" | "down" | "same" | "unknown";
export type MetricRow = {
  metric: string;
  label: string;
  left: string;
  right: string;
  delta: string;
  direction: Direction;
  /** `ok` when the change is the better way for a "lower is better" metric, `bad` when worse, else `neutral`. */
  tone: "ok" | "bad" | "neutral";
};

const GLYPH: Record<Direction, string> = { up: "▲", down: "▼", same: "＝", unknown: "" };
export const directionGlyph = (d: Direction): string => GLYPH[d];

export function metricRows(summary: ComparisonOut["summary"]): MetricRow[] {
  return summary.map((m) => {
    const meta = METRICS[m.metric] ?? {
      label: m.metric,
      format: (n: number) => String(n),
      lowerIsBetter: false,
    };
    const known = m.delta !== null && m.delta !== undefined;
    const direction: Direction = !known
      ? "unknown"
      : m.delta === 0
        ? "same"
        : m.delta! > 0
          ? "up"
          : "down";
    const tone =
      direction === "up" || direction === "down"
        ? meta.lowerIsBetter
          ? direction === "up"
            ? "bad"
            : "ok"
          : "neutral"
        : "neutral";
    const side = (n: number | null | undefined) => (n == null ? DASH : meta.format(n));
    const delta = !known
      ? DASH
      : direction === "same"
        ? "no change"
        : `${direction === "up" ? "+" : "−"}${meta.format(Math.abs(m.delta!))} (${direction === "up" ? "higher" : "lower"})`;
    return {
      metric: m.metric,
      label: meta.label,
      left: side(m.left),
      right: side(m.right),
      delta,
      direction,
      tone,
    };
  });
}

/** Indexes of every row that is not `equal`, in order (the targets of "next difference"). */
export const differenceIndexes = (rows: readonly RowOut[]): number[] =>
  rows.flatMap((r, i) => (r.relation === "equal" ? [] : [i]));

/** The nearest difference after (`1`) or before (`-1`) `from`; wraps nowhere (null at the ends). */
export function nextDifference(
  diffs: readonly number[],
  from: number,
  direction: 1 | -1,
): number | null {
  if (direction === 1) return diffs.find((i) => i > from) ?? null;
  for (let k = diffs.length - 1; k >= 0; k--) if (diffs[k]! < from) return diffs[k]!;
  return null;
}

export const RELATION_LABEL: Record<RowOut["relation"], string> = {
  equal: "Same",
  changed: "Changed",
  left_only: "Only in left",
  right_only: "Only in right",
};

/** One line for a step: the kind, then the label (or a note when the API withheld it), then status and duration. */
export function stepText(s: StepOut): { kind: string; label: string | null; withheld: boolean } {
  return { kind: s.event_type, label: s.label, withheld: s.label_withheld };
}
export const stepMeta = (s: StepOut): string =>
  [s.status, s.duration_ms != null ? formatDuration(s.duration_ms) : null]
    .filter((x): x is string => !!x)
    .join(" · ");

/** First screenful, plus enough rows to include a requested index (so a jump never lands on a hidden row). */
export const ROW_PAGE = 400;
export const visibleCount = (shown: number, total: number, mustInclude: number | null): number =>
  Math.min(total, Math.max(shown, mustInclude === null ? 0 : mustInclude + 1 + 20));

export const scoreText = (s: { score: number | null; label: string | null } | null): string => {
  if (!s) return DASH;
  const parts = [s.score === null ? null : String(Number(s.score.toFixed(3))), s.label].filter(
    (x): x is string => !!x,
  );
  return parts.length ? parts.join(" · ") : DASH;
};

export function deltaText(d: number | null): string {
  if (d === null) return DASH;
  if (d === 0) return "no change";
  return `${d > 0 ? "+" : "−"}${Number(Math.abs(d).toFixed(3))}`;
}

// Phase 22 D12: the divergence line. Built only from what the comparison projection exposes (`StepOut.label` is already
// the role-aware name, with `label_withheld` set when the reader may not see it), so a viewer's sentence never carries content.

export type Part = { text: string; code?: boolean };
const LABEL_MAX = 60;
const clip = (s: string): string => (s.length > LABEL_MAX ? `${s.slice(0, LABEL_MAX - 1)}…` : s);

/** What one side did at a step, as short parts: plain words and, for a name from the run, a `code` part. */
export function describeStep(step: StepOut | null): Part[] {
  if (!step) return [{ text: "had no step here" }];
  const [family = "", ...rest] = step.event_type.split(".");
  const action = rest.join(".");
  const failed = step.event_type.endsWith(".failed");
  const named = (verb: string, fallback: string): Part[] =>
    step.label_withheld
      ? [{ text: `${verb} ${fallback} (details withheld for your role)` }]
      : step.label
        ? [{ text: `${verb} ` }, { text: clip(step.label), code: true }]
        : [{ text: `${verb} ${fallback}` }];
  let parts: Part[];
  switch (family) {
    case "tool":
      parts = named("called", "a tool");
      break;
    case "llm":
      parts = named("called model", "a model");
      break;
    case "shell":
      parts = named("ran", "a shell command");
      break;
    case "file":
      parts = named(
        action.startsWith("created")
          ? "created"
          : action.startsWith("deleted")
            ? "deleted"
            : action.startsWith("read")
              ? "read"
              : "edited",
        "a file",
      );
      break;
    case "span":
      parts = named("ran", "a span");
      break;
    case "agent":
      parts = named("ran agent", "an agent");
      break;
    case "git":
      parts = named("ran git on", "a branch");
      break;
    default:
      parts = [{ text: `recorded ${step.event_type}` }];
  }
  return failed ? [...parts, { text: " (failed)" }] : parts;
}

export type Divergence = { step: number; a: Part[]; b: Part[] };

/** The divergence line's content, or null when the runs took the same steps. */
export function divergenceLine(c: ComparisonOut): Divergence | null {
  if (c.divergence === null) return null;
  const row = c.steps[c.divergence.index];
  return {
    step: c.divergence.index + 1,
    a: describeStep(row?.left ?? null),
    b: describeStep(row?.right ?? null),
  };
}

/** One mono line of deltas (right minus left): duration, cost, calls, retries, files. */
export type DeltaItem = { key: string; label: string; text: string; direction: Direction };
export function deltaLine(summary: ComparisonOut["summary"]): DeltaItem[] {
  const by = new Map(summary.map((m) => [m.metric, m]));
  const one = (key: string, label: string, metric: string): DeltaItem => {
    const m = by.get(metric);
    const meta = METRICS[metric]!;
    const d = m?.delta;
    if (d == null) return { key, label, text: DASH, direction: "unknown" };
    if (d === 0) return { key, label, text: "0", direction: "same" };
    return {
      key,
      label,
      text: `${d > 0 ? "+" : "−"}${meta.format(Math.abs(d))}`,
      direction: d > 0 ? "up" : "down",
    };
  };
  const calls = (): DeltaItem => {
    const parts = ["llm_calls", "tool_calls"].map((k) => by.get(k)?.delta);
    if (parts.some((d) => d == null))
      return { key: "calls", label: "Calls", text: DASH, direction: "unknown" };
    const d = (parts as number[]).reduce((a, b) => a + b, 0);
    return {
      key: "calls",
      label: "Calls",
      text: d === 0 ? "0" : `${d > 0 ? "+" : "−"}${formatInt(Math.abs(d))}`,
      direction: d === 0 ? "same" : d > 0 ? "up" : "down",
    };
  };
  return [
    one("duration", "Duration", "duration_ms"),
    one("cost", "Cost", "estimated_cost_usd"),
    calls(),
    one("retries", "Retries", "retry_count"),
    one("files", "Files", "files_modified"),
  ];
}
