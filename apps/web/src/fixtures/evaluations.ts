import type {
  ComparisonOut,
  EvaluationOut,
  EvaluatorOut,
  EventOut,
  ExperimentOut,
  ExperimentSummaryOut,
  RowOut,
  StepOut,
} from "@/lib/api/types";
import { FIXTURE_PROJECT, type FixtureRun } from "./builder";
import { BASELINE_RUN_N, WRONG_FIX_RUN_N } from "./codingRuns";
import { fid } from "./ids";

/**
 * What the evaluation, comparison and experiment routes return in fixture mode (Phase 11). The data is written once
 * for an owner and projected for a viewer exactly where the API projects it (spec: scores, labels and states are
 * visible to every reader; explanations of human and model-judge rows, path-like or rubric configuration, metadata
 * beyond the allowlist and failure text need `payload.read`). The wording of those projections lives in the API.
 */

export type Viewer = { content: boolean };
export type FixtureReply = { status: number; body: unknown };

const AT = "2026-10-07T06:00:00Z";
const NOW = "2026-10-07T09:00:00Z";
const err = (
  status: number,
  code: string,
  message: string,
  category = "NOT_FOUND",
): FixtureReply => ({
  status,
  body: {
    error: { code, message, category, retryable: false, request_id: "req_fixture", details: {} },
  },
});

// ------------------------------------------------------------------------------------------ evaluators

const EVR = {
  tests: fid("evr", 1),
  cost: fid("evr", 2),
  files: fid("evr", 3),
  judge: fid("evr", 4),
  human: fid("evr", 5),
};
const WITHHELD_CONFIG_KEYS = ["allowed_globs", "forbidden_globs", "required_paths", "rubric"];

const evaluator = (
  id: string,
  name: string,
  kind: EvaluatorOut["kind"],
  implementation: string,
  config: Record<string, unknown>,
  description: string,
): EvaluatorOut => ({
  id,
  name,
  kind,
  implementation,
  version: 1,
  description,
  project_id: FIXTURE_PROJECT,
  config,
  config_withheld: [],
  ground_truth: kind !== "llm_judge",
  archived_at: null,
  created_at: AT,
});

const EVALUATORS: EvaluatorOut[] = [
  evaluator(EVR.tests, "tests-pass", "deterministic", "tests_passed", {}, "Every test run passed."),
  evaluator(EVR.cost, "cost-under-1usd", "metric", "cost_under", { max_usd: 1 }, "Costs under $1."),
  evaluator(
    EVR.files,
    "only-app-files",
    "deterministic",
    "files_touched_only",
    { allowed_globs: ["app/*"], forbidden_globs: [".env*"] },
    "Touches only files under app/.",
  ),
  evaluator(
    EVR.judge,
    "quality-judge",
    "llm_judge",
    "judge",
    { rubric: "Did the change fix the cause, not the symptom?", allowed_labels: ["good", "weak"] },
    "A model's opinion of the fix. Not ground truth.",
  ),
  evaluator(
    EVR.human,
    "reviewer-verdict",
    "human",
    "human",
    { allowed_labels: ["pass", "fail", "unrated"] },
    "A person's verdict.",
  ),
];

export function fixtureEvaluators(viewer: Viewer): EvaluatorOut[] {
  if (viewer.content) return EVALUATORS;
  return EVALUATORS.map((e) => {
    const withheld = WITHHELD_CONFIG_KEYS.filter((k) => k in e.config);
    return {
      ...e,
      config: Object.fromEntries(
        Object.entries(e.config).map(([k, v]) => [k, withheld.includes(k) ? null : v]),
      ),
      config_withheld: withheld,
    };
  });
}

// ----------------------------------------------------------------------------------------- evaluations

type Row = Partial<EvaluationOut> &
  Pick<EvaluationOut, "id" | "run_id" | "evaluator_id" | "state" | "kind">;
const HOSTILE = `Reviewed by hand. <img src=x onerror="alert(1)"> **not bold** [x](javascript:alert(1))`;
const NAME_OF = new Map(EVALUATORS.map((e) => [e.id, e]));

function evaluation(r: Row): EvaluationOut {
  const e = NAME_OF.get(r.evaluator_id!)!;
  return {
    completed_at: r.state === "completed" || r.state === "failed" ? NOW : null,
    created_at: NOW,
    error: null,
    error_code: null,
    evaluator_name: e.name,
    evaluator_version: "1",
    evidence_event_ids: [],
    explanation: null,
    explanation_withheld: false,
    ground_truth: e.ground_truth,
    implementation: e.implementation,
    label: null,
    latest: true,
    metadata: {},
    metadata_withheld: [],
    metrics: {},
    score: null,
    started_at: r.state === "pending" ? null : NOW,
    ...r,
  };
}

const baselineId = fid("run", BASELINE_RUN_N);
const wrongId = fid("run", WRONG_FIX_RUN_N);
const JUDGE_META = {
  judge_model: "fake-judge-1",
  prompt_template_version: "1",
  input_tokens: 1200,
  output_tokens: 40,
  cost_usd: 0.0042,
  raw: "judge reply kept for people who may read content",
};

/** Newest first, like the API. */
function evaluationRows(f: FixtureRun): EvaluationOut[] {
  const run_id = f.run.id;
  const event = (i: number) => f.events[i]?.event_id;
  if (run_id === baselineId) {
    return [
      evaluation({ id: fid("eval", 11), run_id, evaluator_id: EVR.human, kind: "human", state: "completed", score: 1, label: "pass", explanation: HOSTILE }),
      evaluation({ id: fid("eval", 12), run_id, evaluator_id: EVR.judge, kind: "llm_judge", state: "completed", score: 0.8, label: "good", explanation: "The patch refreshes early and keeps the refresh token.", metadata: JUDGE_META }),
      evaluation({ id: fid("eval", 13), run_id, evaluator_id: EVR.cost, kind: "metric", state: "completed", score: 1, label: "pass", explanation: "Cost $0.09 is under the $1.00 limit.", metrics: { cost_usd: 0.09 } }),
      evaluation({ id: fid("eval", 14), run_id, evaluator_id: EVR.tests, kind: "deterministic", state: "completed", score: 1, label: "pass", explanation: "The last test run passed (6 of 6).", evidence_event_ids: [event(30)!].filter(Boolean), metrics: { test_runs: 2 } }),
    ]; // prettier-ignore
  }
  if (run_id === wrongId) {
    return [
      evaluation({ id: fid("eval", 21), run_id, evaluator_id: EVR.judge, kind: "llm_judge", state: "running" }),
      evaluation({ id: fid("eval", 22), run_id, evaluator_id: EVR.cost, kind: "metric", state: "pending" }),
      evaluation({ id: fid("eval", 23), run_id, evaluator_id: EVR.files, kind: "deterministic", state: "failed", error_code: "TIMEOUT", error: "The evaluator exceeded its time budget." }),
      evaluation({ id: fid("eval", 24), run_id, evaluator_id: EVR.tests, kind: "deterministic", state: "completed", score: 0, label: "fail", explanation: "The last test run failed (1 of 6).", metrics: { test_runs: 2 } }),
      evaluation({ id: fid("eval", 25), run_id, evaluator_id: EVR.tests, kind: "deterministic", state: "failed", latest: false, error_code: "JOB_FAILED", error: "The evaluation job did not finish." }),
    ]; // prettier-ignore
  }
  return [];
}

const JUDGE_METADATA_ALLOWED = [
  "judge_model",
  "prompt_template_version",
  "input_tokens",
  "output_tokens",
  "cost_usd",
  "provider",
];

function projectEvaluation(e: EvaluationOut, viewer: Viewer): EvaluationOut {
  if (viewer.content) return e;
  const freeText = e.kind === "human" || e.kind === "llm_judge";
  const hidden = Object.keys(e.metadata).filter((k) => !JUDGE_METADATA_ALLOWED.includes(k));
  return {
    ...e,
    explanation: freeText ? null : e.explanation,
    explanation_withheld: freeText && e.state === "completed",
    error: null,
    metadata: Object.fromEntries(
      Object.entries(e.metadata).filter(([k]) => JUDGE_METADATA_ALLOWED.includes(k)),
    ),
    metadata_withheld: hidden,
  };
}

export function fixtureRunEvaluations(f: FixtureRun, viewer: Viewer) {
  return { items: evaluationRows(f).map((e) => projectEvaluation(e, viewer)), truncated: false };
}

// ------------------------------------------------------------------------------------------ comparison

const key = (s: StepOut, content: boolean) =>
  `${s.event_type}|${content || s.family === "llm" || s.family === "tool" ? s.label : ""}`;

function stepsOf(f: FixtureRun, viewer: Viewer): StepOut[] {
  const t0 = Date.parse(f.events[0]!.occurred_at);
  const kept = f.events.filter((e) =>
    /^(llm\.request\.completed|tool\.call\.completed|file\.(read|modified)|shell\.command\.(completed|failed)|retry\.attempted|git\.(commit|push)|run\.(completed|failed))$/.test(
      e.event_type,
    ),
  );
  return kept.map((e: EventOut) => {
    const family = e.event_type.split(".")[0]!;
    const raw =
      (e.attributes["tool.name"] as string | undefined) ??
      (e.attributes["llm.model"] as string | undefined) ??
      (e.attributes["file.path"] as string | undefined) ??
      (e.attributes["shell.command"] as string | undefined) ??
      (e.attributes["git.branch"] as string | undefined) ??
      null;
    const isContent = family === "file" || family === "shell" || family === "git";
    const withheld = isContent && !viewer.content && raw !== null;
    return {
      event_id: e.event_id,
      span_id: e.span_id ?? null,
      event_type: e.event_type,
      family,
      label: withheld ? null : raw,
      label_withheld: withheld,
      status: e.status ?? null,
      duration_ms: e.duration_ms ?? null,
      offset_ms: Date.parse(e.occurred_at) - t0,
    };
  });
}

/** Prefix and suffix are matched, the middle is zipped into `changed` rows: enough for a deterministic fixture. */
function align(left: StepOut[], right: StepOut[], content: boolean): RowOut[] {
  const same = (a: StepOut, b: StepOut) => key(a, content) === key(b, content);
  let p = 0;
  while (p < left.length && p < right.length && same(left[p]!, right[p]!)) p++;
  let s = 0;
  while (
    s < left.length - p &&
    s < right.length - p &&
    same(left[left.length - 1 - s]!, right[right.length - 1 - s]!)
  )
    s++;
  const rows: RowOut[] = left
    .slice(0, p)
    .map((l, i) => ({ relation: "equal", left: l, right: right[i]! }));
  const ml = left.slice(p, left.length - s);
  const mr = right.slice(p, right.length - s);
  for (let i = 0; i < Math.max(ml.length, mr.length); i++) {
    const l = ml[i] ?? null;
    const r = mr[i] ?? null;
    rows.push({ relation: l && r ? "changed" : l ? "left_only" : "right_only", left: l, right: r });
  }
  for (let i = 0; i < s; i++)
    rows.push({
      relation: "equal",
      left: left[left.length - s + i]!,
      right: right[right.length - s + i]!,
    });
  return rows;
}

const filesOf = (f: FixtureRun) => [
  ...new Set(
    f.events
      .filter((e) => e.event_type === "file.modified")
      .map((e) => String(e.attributes["file.path"])),
  ),
];

export function fixtureComparison(a: FixtureRun, b: FixtureRun, viewer: Viewer): ComparisonOut {
  const left = stepsOf(a, viewer);
  const right = stepsOf(b, viewer);
  const steps = align(left, right, viewer.content);
  const divergence = steps.findIndex((r) => r.relation !== "equal");
  const outcome = steps.findIndex((r) => r.left && r.right && r.left.status !== r.right.status);
  const metric = (name: string, l: unknown, r: unknown) => {
    const ln = typeof l === "number" ? l : null;
    const rn = typeof r === "number" ? r : null;
    return {
      metric: name,
      left: ln,
      right: rn,
      delta: ln !== null && rn !== null ? rn - ln : null,
    };
  };
  const fa = filesOf(a);
  const fb = filesOf(b);
  const evalsOf = (f: FixtureRun) =>
    new Map(
      evaluationRows(f)
        .filter((e) => e.latest && e.state === "completed")
        .map((e) => [e.evaluator_id!, e] as const),
    );
  const ea = evalsOf(a);
  const eb = evalsOf(b);
  const evaluations = [...new Set([...ea.keys(), ...eb.keys()])].map((id) => {
    const info = NAME_OF.get(id)!;
    const l = ea.get(id);
    const r = eb.get(id);
    return {
      evaluator_id: id,
      evaluator_name: info.name,
      kind: info.kind,
      ground_truth: info.ground_truth,
      left: l ? { score: l.score, label: l.label } : null,
      right: r ? { score: r.score, label: r.label } : null,
      delta: l?.score != null && r?.score != null ? r.score - l.score : null,
    };
  });
  const side = (f: FixtureRun, n: number) => ({
    run_id: f.run.id,
    status: f.run.status,
    steps: n,
    truncated: false,
    truncated_at_event: null,
  });
  return {
    alignment: viewer.content ? "content" : "metadata",
    alignment_quality: "full",
    cost_by_model: [
      {
        model: "scripted-agent-1",
        left: Number(a.run.summary["estimated_cost_usd"] ?? 0),
        right: Number(b.run.summary["estimated_cost_usd"] ?? 0),
        delta:
          Number(b.run.summary["estimated_cost_usd"] ?? 0) -
          Number(a.run.summary["estimated_cost_usd"] ?? 0),
      },
    ],
    divergence:
      divergence >= 0
        ? { index: divergence, relation: steps[divergence]!.relation as "changed" }
        : null,
    evaluations,
    files: viewer.content
      ? {
          both: fa.filter((x) => fb.includes(x)),
          only_left: fa.filter((x) => !fb.includes(x)),
          only_right: fb.filter((x) => !fa.includes(x)),
          left_count: fa.length,
          right_count: fb.length,
          paths_withheld: false,
          truncated: false,
        }
      : {
          both: [],
          only_left: [],
          only_right: [],
          left_count: fa.length,
          right_count: fb.length,
          paths_withheld: true,
          truncated: false,
        },
    first_outcome_difference: outcome >= 0 ? outcome : null,
    left: side(a, left.length),
    right: side(b, right.length),
    steps,
    summary: [
      "estimated_cost_usd",
      "duration_ms",
      "llm_calls",
      "tool_calls",
      "retry_count",
      "error_count",
    ].map((m) =>
      metric(m === "estimated_cost_usd" ? "cost_usd" : m, a.run.summary[m], b.run.summary[m]),
    ),
  };
}

// ---------------------------------------------------------------------------------------- experiments

const dist = (v: number) => ({ mean: v, median: v, p95: v });
const EXP_CONTROLLED = fid("exp", 1);
const EXP_UNCONTROLLED = fid("exp", 2);
const VAR = { base: fid("var", 1), cand: fid("var", 2), b2: fid("var", 3), c2: fid("var", 4) };

function attached(f: FixtureRun, variant_id: string) {
  return {
    run_id: f.run.id,
    variant_id,
    status: f.run.status,
    started_at: f.run.started_at,
    added_at: NOW,
  };
}

export function fixtureExperiments(runs: FixtureRun[], viewer: Viewer): ExperimentOut[] {
  const base = runs.find((r) => r.run.id === baselineId)!;
  const wrong = runs.find((r) => r.run.id === wrongId)!;
  const info = (id: string) => {
    const e = NAME_OF.get(id)!;
    return { id: e.id, name: e.name, kind: e.kind, ground_truth: e.ground_truth, archived: false };
  };
  const metrics = (variant_id: string, f: FixtureRun | null) => ({
    variant_id,
    runs: f ? 1 : 0,
    finished: f ? 1 : 0,
    success_rate: f ? (f.run.status === "SUCCESS" ? 1 : 0) : null,
    retry_rate: f ? (Number(f.run.summary["retry_count"]) > 0 ? 1 : 0) : null,
    tool_calls_mean: f ? Number(f.run.summary["tool_calls"]) : null,
    cost_usd: dist(f ? Number(f.run.summary["estimated_cost_usd"]) : 0),
    duration_ms: dist(f ? Number(f.run.summary["duration_ms"]) : 0),
    failure_categories: (f && f.run.status !== "SUCCESS" ? { FAILED: 1 } : {}) as Record<
      string,
      number
    >,
    evaluations: f
      ? evaluationRows(f)
          .filter((e) => e.latest && e.state === "completed" && e.kind !== "human")
          .map((e) => ({
            evaluator_id: e.evaluator_id!,
            completed: 1,
            failed: 0,
            mean_score: e.score,
            pass_rate: e.label === "pass" ? 1 : e.label === "fail" ? 0 : null,
          }))
      : [],
  });
  const all: ExperimentOut[] = [
    {
      id: EXP_CONTROLLED,
      project_id: FIXTURE_PROJECT,
      name: "Session fix: second patch",
      description: "One variable changed: the fix strategy.",
      dataset_label: "oauth-session-expiry",
      created_at: AT,
      archived_at: null,
      evaluators: [info(EVR.tests), info(EVR.cost)],
      variants: [
        { id: VAR.base, name: "baseline", role: "baseline", config: { model: "scripted-agent-1", strategy: "refresh-token" }, changed_variables: null, controlled: null, run_count: 1 },
        { id: VAR.cand, name: "wrong fix", role: "candidate", config: { model: "scripted-agent-1", strategy: "docstring" }, changed_variables: ["strategy"], controlled: true, run_count: 1 },
      ], // prettier-ignore
      runs: [attached(base, VAR.base), attached(wrong, VAR.cand)],
      metrics: [metrics(VAR.base, base), metrics(VAR.cand, wrong)],
      warnings: [],
    },
    {
      id: EXP_UNCONTROLLED,
      project_id: FIXTURE_PROJECT,
      name: "Model and prompt together <b>not</b> controlled",
      description: null,
      dataset_label: null,
      created_at: AT,
      archived_at: null,
      evaluators: [info(EVR.tests)],
      variants: [
        { id: VAR.b2, name: "baseline", role: "baseline", config: { model: "a", prompt: "v1" }, changed_variables: null, controlled: null, run_count: 0 },
        { id: VAR.c2, name: "candidate", role: "candidate", config: { model: "b", prompt: "v2" }, changed_variables: ["model", "prompt"], controlled: false, run_count: 0 },
      ], // prettier-ignore
      runs: [],
      metrics: [metrics(VAR.b2, null), metrics(VAR.c2, null)],
      warnings: [{ code: "not_single_variable", keys: ["model", "prompt"], variant_id: VAR.c2 }],
    },
  ];
  void viewer; // experiments carry no free text: the same body for every reader
  return all;
}

export function fixtureExperimentList(
  runs: FixtureRun[],
  viewer: Viewer,
): { items: ExperimentSummaryOut[] } {
  return {
    items: fixtureExperiments(runs, viewer).map((e) => ({
      id: e.id,
      project_id: e.project_id,
      name: e.name,
      description: e.description,
      dataset_label: e.dataset_label,
      created_at: e.created_at,
      archived_at: e.archived_at,
      variant_count: e.variants.length,
      run_count: e.runs.length,
    })),
  };
}

/** `evaluators`, `experiments`: the workspace-level listings. Returns null for a path this module does not serve. */
export function fixtureWorkspaceReply(
  segments: string[],
  runs: FixtureRun[],
  viewer: Viewer,
): FixtureReply | null {
  const [, kind, id, ...rest] = segments;
  if (kind === "evaluators" && !id)
    return { status: 200, body: { items: fixtureEvaluators(viewer) } };
  if (kind === "experiments" && !id)
    return { status: 200, body: fixtureExperimentList(runs, viewer) };
  if (kind === "experiments" && id && rest.length === 0) {
    const e = fixtureExperiments(runs, viewer).find((x) => x.id === id);
    return e ? { status: 200, body: e } : err(404, "EXPERIMENT_NOT_FOUND", "Experiment not found.");
  }
  return null;
}
