import type {
  DimensionOut,
  EventOut,
  FindingOut,
  FindingsBadge,
  RunFindingsOut,
} from "@/lib/api/types";
import type { FixtureRun } from "./builder";
import { fid } from "./ids";

/**
 * What the findings route returns for the fixture runs. The three pathological runs carry hand-written findings
 * whose evidence cites real events of the run (by sequence number); every other run is analysed and has none, apart
 * from the running run (analysis in progress) and the approval run (not analysed yet), which exercise those states.
 * Wording mirrors the API's `describe()`; the Python fixtures under apps/api/tests/fixtures/pathological are the
 * source of truth for the rules.
 */

type Spec = Omit<
  FindingOut,
  | "id"
  | "run_id"
  | "created_at"
  | "updated_at"
  | "fingerprint"
  | "evidence_event_ids"
  | "start_event_id"
  | "end_event_id"
> & { start: number; end: number; cited?: number[] };

const THRESHOLDS = {
  loop_min_repetitions: 3,
  loop_min_repetitions_single: 8,
  loop_min_tokens: 6,
  loop_max_pattern_length: 8,
};

function build(f: FixtureRun, specs: Spec[]): FindingOut[] {
  const at = f.run.updated_at;
  const id = (seq: number) => f.events[seq - 1]!.event_id;
  return specs.map(({ start, end, cited, ...spec }, i) => ({
    ...spec,
    id: fid("fnd", Number(f.run.id.slice(-6)) * 100 + i + 1),
    run_id: f.run.id,
    start_event_id: id(start),
    end_event_id: id(end),
    evidence_event_ids: [...new Set([start, end, ...(cited ?? [])])].map(id),
    fingerprint: (`${i + 1}`.padStart(2, "0") + "a1b2c3d4").repeat(8).slice(0, 64),
    created_at: at,
    updated_at: at,
  }));
}

const evt = (f: FixtureRun, seq: number): EventOut => f.events[seq - 1]!;

function findingsFor(f: FixtureRun): FindingOut[] {
  switch (f.run.name) {
    case "Resolve flaky docs search": {
      // search_docs, read_file, llm x 6: first closers at 4, 10, 16, 22, 28, 34; the last closer is 38.
      const starts = [4, 10, 16, 22, 28, 34];
      const ms = Date.parse(evt(f, 38).occurred_at) - Date.parse(evt(f, 4).occurred_at);
      return build(f, [
        {
          detector: "repeated-sequence",
          detector_version: "repeated-sequence@1.0.0",
          finding_type: "possible_loop",
          kind: "probable",
          severity: "info",
          confidence: 0.8,
          title: "Probable: the agent may be looping",
          message: `The agent repeated tool:search_docs -> tool:read_file -> llm 6 times in ${Math.round(ms / 1000)} s.`,
          start: 4,
          end: 38,
          cited: starts,
          evidence: {
            agent_id: f.run.agent_id,
            pattern: ["tool:search_docs", "tool:read_file", "llm"],
            repetitions: 6,
            event_ids: starts.map((s) => evt(f, s).event_id),
            duration_ms: ms,
            thresholds: THRESHOLDS,
          },
        },
      ]);
    }
    case "Sync customer records": {
      const failures = [
        { seq: 8, tool: "upsert_record" },
        { seq: 11, tool: "upsert_record" },
        { seq: 14, tool: "upsert_record" },
      ];
      return build(f, [
        {
          detector: "tool-failure-cascade",
          detector_version: "tool-failure-cascade@1.0.0",
          finding_type: "tool_failure_cascade",
          kind: "deterministic",
          severity: "warning",
          confidence: 1,
          title: "failures in a row",
          message:
            "3 failures in 6 calls, with 2 retries between. It then used bulk_import, verify_import.",
          start: 8,
          end: 14,
          cited: failures.map((x) => x.seq),
          evidence: {
            agent_id: f.run.agent_id,
            failures: failures.map((x) => ({
              event_id: evt(f, x.seq).event_id,
              event_type: "tool.call.failed",
              tool_name: x.tool,
              error_type: "Timeout",
            })),
            calls_in_window: 6,
            retries_between: 2,
            alternates: ["bulk_import", "verify_import"],
            thresholds: { cascade_min_failures: 3, cascade_window_calls: 10 },
          },
        },
      ]);
    }
    case "Fix flaky checkout test": {
      const chain = [
        { seq: 6, role: "mutation" },
        { seq: 9, role: "retry" },
        { seq: 11, role: "root" },
        { seq: 12, role: "terminal" },
      ];
      return build(f, [
        {
          detector: "failure-chain",
          detector_version: "failure-chain@1.0.0",
          finding_type: "failure_chain",
          kind: "deterministic",
          severity: "critical",
          confidence: 1,
          title: "the run failed: a command failed",
          message:
            "1 retry followed the first error, and 1 change to files or the repository were made before the end.",
          start: 11,
          end: 12,
          cited: chain.map((c) => c.seq),
          evidence: {
            category: "command_failure",
            terminal_event_id: evt(f, 12).event_id,
            root_event_id: evt(f, 11).event_id,
            chain: chain.map((c) => ({
              event_id: evt(f, c.seq).event_id,
              event_type: evt(f, c.seq).event_type,
              role: c.role,
              span_id: evt(f, c.seq).span_id,
            })),
            retries_caused: 1,
            retries_unattributed: 0,
            mutations: 1,
          },
        },
        {
          detector: "failure-chain",
          detector_version: "failure-chain@1.0.0",
          finding_type: "probable_cause",
          kind: "probable",
          severity: "warning",
          confidence: 0.5,
          title: "Probable: a code change was followed by failing tests",
          message: "This may explain the failure.",
          start: 6,
          end: 8,
          evidence: {
            cause: "code_change_before_test_failure",
            event_ids: [evt(f, 6).event_id, evt(f, 8).event_id],
          },
        },
      ]);
    }
    default:
      return [];
  }
}

const SEVERITY_RANK = { info: 0, warning: 1, critical: 2 } as const;

function health(f: FixtureRun, items: FindingOut[], current: boolean): RunFindingsOut["health"] {
  const s = f.run.summary;
  const failed = ["FAILED", "TIMED_OUT", "BLOCKED"].includes(f.run.status);
  const errors = Number(s["error_count"] ?? 0);
  const retries = Number(s["retry_count"] ?? 0);
  const tools = f.events.filter((e) => /^tool\.call\.(completed|failed)$/.test(e.event_type));
  const toolsOk = tools.filter((e) => e.status === "success").length;
  const cost = Number(s["estimated_cost_usd"] ?? 0);
  const noData = (name: string, why: string): DimensionOut => ({
    name,
    status: "no_data",
    inputs: {},
    rule: why,
    finding_ids: [],
  });
  const why = current ? "" : "Not analysed yet.";
  const byType = (types: string[]) => items.filter((i) => types.includes(i.finding_type));
  const loops = byType(["possible_loop", "reported_loop", "identical_request"]);
  const ratio = tools.length ? toolsOk / tools.length : 1;
  const dims: DimensionOut[] = [
    {
      name: "reliability",
      status: failed ? "bad" : errors + retries > 0 ? "warn" : "ok",
      inputs: { status: f.run.status, error_count: errors, retry_count: retries },
      rule: failed
        ? "The run ended FAILED, TIMED_OUT or BLOCKED."
        : errors + retries > 0
          ? "The run had errors or retries but did not fail."
          : "No failure, error or retry.",
      finding_ids: [],
    },
    tools.length === 0
      ? noData("tool_reliability", "No tool call has finished.")
      : {
          name: "tool_reliability",
          status: ratio >= 0.95 ? "ok" : ratio >= 0.8 ? "warn" : "bad",
          inputs: {
            tool_spans_ok: toolsOk,
            tool_spans_finished: tools.length,
            success_ratio: +ratio.toFixed(3),
          },
          rule:
            ratio >= 0.95
              ? "At least 95% of tool calls succeeded."
              : ratio >= 0.8
                ? "80% to 95% of tool calls succeeded."
                : "Fewer than 80% of tool calls succeeded.",
          finding_ids: [],
        },
    cost <= 0
      ? noData("cost_efficiency", "The run has no priced cost.")
      : {
          name: "cost_efficiency",
          status: "ok",
          inputs: { cost_usd: String(cost), retry_cost_usd: "0", retry_cost_ratio: 0 },
          rule: "Retries cost under 10% of the run.",
          finding_ids: [],
        },
    current
      ? {
          name: "loops",
          status: loops.some((i) => i.severity === "critical")
            ? "bad"
            : loops.length
              ? "warn"
              : "ok",
          inputs: { findings: loops.length },
          rule: loops.length
            ? "A probable loop, a reported loop or identical requests were found."
            : "No loop or identical requests were found.",
          finding_ids: loops.map((i) => i.id),
        }
      : noData("loops", why),
    current
      ? {
          name: "context",
          status: "ok",
          inputs: { findings: 0 },
          rule: "The context did not grow unusually (or only slightly).",
          finding_ids: [],
        }
      : noData("context", why),
  ];
  const rank = { no_data: 0, ok: 1, warn: 2, bad: 3 } as const;
  const overall = dims.reduce(
    (w, d) => (rank[d.status] > rank[w] ? d.status : w),
    "no_data" as DimensionOut["status"],
  );
  return { overall, dimensions: dims };
}

/** Attach the findings response and the list badge to a fixture run. */
export function withFindings(f: FixtureRun): FixtureRun {
  const status: RunFindingsOut["state"]["status"] =
    f.run.status === "RUNNING"
      ? "processing"
      : f.run.status === "WAITING_FOR_APPROVAL"
        ? "none"
        : "current";
  const items = findingsFor(f);
  const count = items.length;
  const worst = items.reduce<FindingOut["severity"] | null>(
    (w, i) => (w == null || SEVERITY_RANK[i.severity] > SEVERITY_RANK[w] ? i.severity : w),
    null,
  );
  const analysed = status === "current";
  const badge: FindingsBadge = {
    count,
    max_severity: worst,
    state: analysed ? "current" : "none",
  };
  const findings: RunFindingsOut = {
    run_id: f.run.id,
    state: {
      status,
      detectors_version: analysed ? 1 : null,
      summary_version_seen: analysed ? 2 : null,
      computed_at: analysed ? f.run.updated_at : null,
      event_count_seen: analysed ? f.events.length : null,
      truncated: false,
    },
    health: health(f, items, analysed),
    items,
  };
  return { ...f, run: { ...f.run, findings: badge }, findings };
}
