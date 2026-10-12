import type { EvaluationOut, EvaluationState, EvaluatorKind, EvaluatorOut } from "./api/types";

/**
 * Presentation rules for evaluations. No role logic lives here: what a reader may see is decided by the API (it nulls
 * free text and lists what it withheld), and what a person may do comes from `/v1/me` through `useCan`.
 */

export const STATE_META: Record<EvaluationState, { glyph: string; label: string; tone: string }> = {
  pending: { glyph: "◌", label: "Pending", tone: "neutral" },
  running: { glyph: "●", label: "Running", tone: "info" },
  completed: { glyph: "✓", label: "Completed", tone: "ok" },
  failed: { glyph: "✕", label: "Failed", tone: "bad" },
};

export const KIND_LABEL: Record<EvaluatorKind, string> = {
  deterministic: "Rule",
  metric: "Metric",
  human: "Human verdict",
  llm_judge: "Model judge",
};

/** A model judge's output is an opinion. The API says so with `ground_truth: false`; the badge follows the field. */
export const isOpinion = (e: Pick<EvaluationOut, "ground_truth">): boolean => !e.ground_truth;
export const JUDGE_BADGE = "model judge, not ground truth";

export const formatScore = (score: number | null): string =>
  score === null
    ? "—"
    : Number.isInteger(score)
      ? score.toFixed(1)
      : String(Number(score.toFixed(3)));

/** Pass / fail style labels keep their colour semantics; anything else is neutral. Text always carries the meaning. */
export function labelTone(label: string | null): string {
  switch (label?.toLowerCase()) {
    case "pass":
    case "good":
      return "ok";
    case "fail":
    case "bad":
    case "weak":
      return "bad";
    default:
      return "neutral";
  }
}

/** The newest row of each evaluator (`latest`), then the older rows. The API sends newest first. */
export function splitEvaluations(items: readonly EvaluationOut[]) {
  const latest = items.filter((e) => e.latest);
  return { latest, earlier: items.filter((e) => !e.latest) };
}

export const hasLive = (items: readonly EvaluationOut[]): boolean =>
  items.some((e) => e.state === "pending" || e.state === "running");

/** The evaluators a person can start on a run: not archived, not a human one (a person records a verdict instead). */
export const runnable = (evaluators: readonly EvaluatorOut[]): EvaluatorOut[] =>
  evaluators.filter((e) => e.archived_at === null && e.kind !== "human");
export const verdictable = (evaluators: readonly EvaluatorOut[]): EvaluatorOut[] =>
  evaluators.filter((e) => e.archived_at === null && e.kind === "human");

/** `allowed_labels` of a human evaluator, defensively read from its (API-validated) configuration. */
export function allowedLabels(e: EvaluatorOut): string[] {
  const raw = e.config["allowed_labels"];
  return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : [];
}

/** The facts of a judge row the API allows every reader to see. Values are shown as text. */
export function judgeFacts(e: EvaluationOut): [string, string][] {
  const m = e.metadata;
  const out: [string, string][] = [];
  const text = (v: unknown) => (typeof v === "string" || typeof v === "number" ? String(v) : null);
  const model = text(m["judge_model"]);
  if (model) out.push(["Judge model", model]);
  const inT = text(m["input_tokens"]);
  const outT = text(m["output_tokens"]);
  if (inT || outT) out.push(["Tokens", `${inT ?? "?"} in / ${outT ?? "?"} out`]);
  const cost = typeof m["cost_usd"] === "number" ? m["cost_usd"] : null;
  if (cost !== null) out.push(["Judge cost", `$${cost.toFixed(4)}`]);
  return out;
}
