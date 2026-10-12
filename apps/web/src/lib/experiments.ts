import type { ExperimentOut, VariantIn, VariantMetricsOut, WarningOut } from "./api/types";
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
