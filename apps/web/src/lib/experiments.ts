import type { ExperimentOut, VariantIn, VariantMetricsOut, WarningOut } from "./api/types";
import { experiments } from "@/copy/experiments";
import { DASH, formatCost, formatDuration } from "./format";

/** Presentation and form rules for experiments. The control check itself is the API's (`controlled`, `changed_variables`). */

/** The most runs one "Evaluate all" call starts evaluations for; the response says how many remain. */
export const EVALUATE_CAP = 500;

export const WARNING_TEXT: Record<string, string> = {
  not_single_variable: "More than one setting differs from the baseline",
  variant_models_vary: "Runs in this variant used different models",
  variant_agents_vary: "Runs in this variant came from different agents",
  unevaluated_runs: "Some attached runs have no completed evaluation yet",
};
export const warningText = (w: WarningOut): string => WARNING_TEXT[w.code] ?? w.code;

export type Control = {
  state: "single" | "multiple" | "none" | "no-candidates";
  /** Candidates and the settings that differ, for the banner. */
  changes: { variant: string; keys: string[] }[];
};

/** The banner's input: derived only from the API's per-variant facts. */
export function controlOf(e: Pick<ExperimentOut, "variants">): Control {
  const candidates = e.variants.filter((v) => v.role === "candidate");
  if (candidates.length === 0) return { state: "no-candidates", changes: [] };
  const changes = candidates.map((v) => ({ variant: v.name, keys: v.changed_variables ?? [] }));
  if (candidates.every((v) => v.controlled === true)) return { state: "single", changes };
  if (candidates.some((v) => v.controlled === false)) return { state: "multiple", changes };
  return { state: "none", changes };
}

export const pct = (r: number | null | undefined): string =>
  r == null ? DASH : `${Math.round(r * 100)}%`;
export const num = (n: number | null | undefined, digits = 2): string =>
  n == null ? DASH : String(Number(n.toFixed(digits)));

export type DistCell = { mean: string; median: string; p95: string };
export const distCost = (d: VariantMetricsOut["cost_usd"]): DistCell => ({
  mean: formatCost(d.mean),
  median: formatCost(d.median),
  p95: formatCost(d.p95),
});
export const distDuration = (d: VariantMetricsOut["duration_ms"]): DistCell => ({
  mean: formatDuration(d.mean),
  median: formatDuration(d.median),
  p95: formatDuration(d.p95),
});

export const failureText = (m: VariantMetricsOut): string => {
  const entries = Object.entries(m.failure_categories).sort(([a], [b]) => a.localeCompare(b));
  return entries.length === 0 ? "none" : entries.map(([k, n]) => `${k} ${n}`).join(", ");
};

export const configEntries = (config: Record<string, string>): [string, string][] =>
  Object.entries(config).sort(([a], [b]) => a.localeCompare(b));

/**
 * `key=value` lines to a variant config. The API validates the keys and values again; this only splits, so a
 * mistake gets an inline message instead of a round trip.
 */
export function parseConfig(text: string): { config: VariantIn["config"]; error: string | null } {
  const config: Record<string, string> = {};
  for (const [i, raw] of text.split("\n").entries()) {
    const line = raw.trim();
    if (!line) continue;
    const at = line.indexOf("=");
    if (at <= 0) return { config: {}, error: `Line ${i + 1}: write it as key=value.` };
    const key = line.slice(0, at).trim();
    if (key in config) return { config: {}, error: `Line ${i + 1}: "${key}" is set twice.` };
    config[key] = line.slice(at + 1).trim();
  }
  return { config, error: null };
}

/** Both pointers of a baseline-vs-run comparison for a candidate variant's run: the baseline's first run. */
export function baselineRunId(e: Pick<ExperimentOut, "variants" | "runs">): string | null {
  const baseline = e.variants.find((v) => v.role === "baseline");
  return e.runs.find((r) => r.variant_id === baseline?.id)?.run_id ?? null;
}

/** A cost as it is printed, as a number, so two costs that read the same compare as equal. */
const shownCost = (usd: number): number => Number(formatCost(usd).slice(1));

/** The variant with the lowest printed median cost, or null when none is costed or the lowest is shared (the cost chart's mark). */
export function cheapestOf(rows: { id: string; median: number | null }[]): string | null {
  const costed = rows.flatMap((r) =>
    r.median == null ? [] : [{ id: r.id, c: shownCost(r.median) }],
  );
  if (costed.length < 2) return null;
  const low = Math.min(...costed.map((r) => r.c));
  const at = costed.filter((r) => r.c === low);
  return at.length === 1 ? at[0]!.id : null;
}

/** Below this many finished runs in either variant the sentence says the comparison is too thin to rely on. */
export const SMALL_SAMPLE = 5;

export type Verdict = {
  kind: "none" | "lead" | "tie";
  /** The variant that leads; only for `lead`. */
  leader: string | null;
  text: string;
};

/**
 * The winner in words: which variant did best on success rate, and when that ties, on median cost. Only the API's per-variant
 * metrics are read; a variant with no finished run takes no part. The comparison is the top two variants, the sentence says
 * how many finished runs it rests on, and it never claims a cause (the control banner says whether the comparison is controlled).
 */
export function verdictOf(e: Pick<ExperimentOut, "variants" | "metrics">): Verdict {
  const v = experiments.verdict;
  const by = new Map(e.metrics.map((m) => [m.variant_id, m] as const));
  const rows = e.variants.flatMap((variant) => {
    const m = by.get(variant.id);
    return m && m.finished > 0 && m.success_rate != null ? [{ variant, m }] : [];
  });
  if (rows.length < 2) return { kind: "none", leader: null, text: v.none };
  // Decide on what the sentence prints: two rates that both read "80%", or two costs that both read "$0.0120", are a tie.
  const rate = (r: (typeof rows)[number]) => Math.round(r.m.success_rate! * 100);
  const cost = (r: (typeof rows)[number]) =>
    r.m.cost_usd.median == null ? Number.POSITIVE_INFINITY : shownCost(r.m.cost_usd.median);
  const ranked = [...rows].sort((x, y) => rate(y) - rate(x) || cost(x) - cost(y));
  const [a, b] = [ranked[0]!, ranked[1]!];
  const [na, nb] = [a.variant.name, b.variant.name];
  const of = rows.length > 2 ? ` ${v.of(rows.length)}` : "";
  const basis = `${v.basis(na, a.m.finished, nb, b.m.finished)}${of}`;
  const thin = Math.min(a.m.finished, b.m.finished) < SMALL_SAMPLE ? ` ${v.small}` : "";
  const [ca, cb] = [a.m.cost_usd.median, b.m.cost_usd.median];
  const costed = ca != null && cb != null;
  const [sa, sb] = costed ? [shownCost(ca), shownCost(cb)] : [0, 0];
  const done = (r: Verdict): Verdict => ({
    ...r,
    text: r.text.charAt(0).toUpperCase() + r.text.slice(1),
  });
  if (rate(a) !== rate(b)) {
    const costLine = !costed
      ? ""
      : sa < sb
        ? ` ${v.costLess(formatCost(ca), formatCost(cb))}`
        : sa > sb
          ? ` ${v.costMore(formatCost(ca), formatCost(cb))}`
          : "";
    return done({
      kind: "lead",
      leader: a.variant.id,
      text: `${v.leadsOnSuccess(na, pct(a.m.success_rate), nb, pct(b.m.success_rate))}${costLine} ${basis}${thin}`,
    });
  }
  if (costed && sa < sb)
    return done({
      kind: "lead",
      leader: a.variant.id,
      text: `${v.leadsOnCost(na, pct(a.m.success_rate), nb, formatCost(ca), formatCost(cb))} ${basis}${thin}`,
    });
  const same = costed && sa === sb ? ` ${v.costSame(formatCost(ca))}` : "";
  return done({
    kind: "tie",
    leader: null,
    text: `${v.tie(na, nb, pct(a.m.success_rate))}${same} ${basis}${thin}`,
  });
}
