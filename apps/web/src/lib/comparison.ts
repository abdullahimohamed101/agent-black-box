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
