import type { components } from "@/lib/api/schema";
import { isActive, type RunOut } from "@/lib/api/types";
import { num } from "@/lib/format";
import { fixtureRuns } from "./index";

type S = components["schemas"];
const FINISHED = new Set(["SUCCESS", "FAILED", "TIMED_OUT", "BLOCKED"]);
const rate = (a: number, b: number) => (b ? a / b : null);
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);
const s = (r: RunOut, k: string) => num(r.summary[k]) ?? 0;
/** Fixture runs predate cost lines: assume a share of the cost of a retried run is retry cost. */
const retryUsd = (r: RunOut) =>
  s(r, "retry_cost_usd") || (s(r, "retry_count") > 0 ? s(r, "estimated_cost_usd") * 0.3 : 0);
const day = (r: RunOut) => r.started_at.slice(0, 10);

/** The analytics API (api-v1.md) computed over the deterministic fixture runs; a stand-in for the server. */
export function fixtureAnalytics(kind: string, q: URLSearchParams): unknown {
  let runs = fixtureRuns().map((f) => f.run);
  const project = q.get("project_id");
  if (project) runs = runs.filter((r) => r.project_id === project);
  const agent = q.get("agent_id");
  if (agent) runs = runs.filter((r) => r.agent_id === agent);
  const top = Math.min(Number(q.get("top") ?? 10) || 10, 50);
  const times = runs.map((r) => Date.parse(r.started_at));
  const window = {
    start: new Date(Math.min(...times, Date.now()) - 86_400_000).toISOString(),
    end: new Date(Math.max(...times, Date.now())).toISOString(),
  };
  const count = (st: string) => runs.filter((r) => r.status === st).length;
  const counts: S["RunCounts"] = {
    total: runs.length,
    active: runs.filter((r) => isActive(r.status)).length,
    finished: runs.filter((r) => FINISHED.has(r.status)).length,
    success: count("SUCCESS"),
    failed: count("FAILED"),
    timed_out: count("TIMED_OUT"),
    blocked: count("BLOCKED"),
    cancelled: count("CANCELLED"),
  };
  const rates: S["Rates"] = {
    success_rate: rate(counts.success, counts.finished),
    failure_rate: rate(counts.failed + counts.blocked, counts.finished),
    timeout_rate: rate(counts.timed_out, counts.finished),
    retry_rate: rate(runs.filter((r) => s(r, "retry_count") > 0).length, runs.length),
  };
  const total = sum(runs.map((r) => s(r, "estimated_cost_usd")));
  const retry = sum(runs.map((r) => retryUsd(r)));
  const headline: S["CostHeadline"] = {
    total_usd: total,
    per_run_usd: rate(total, runs.length),
    per_successful_run_usd: rate(
      sum(runs.filter((r) => r.status === "SUCCESS").map((r) => s(r, "estimated_cost_usd"))),
      counts.success,
    ),
    retry_usd: retry,
    retry_share: rate(retry, total),
    unpriced_calls: 0,
    unrebuilt_runs: 0,
  };
  const durations = runs
    .filter((r) => FINISHED.has(r.status) && r.duration_ms != null)
    .map((r) => r.duration_ms as number)
    .sort((a, b) => a - b);
  const pct = (q2: number) =>
    durations.length ? durations[Math.floor(q2 * (durations.length - 1))]! : null;
  const days = [...new Set(runs.map(day))].sort();
  const avg = (k: string) => (runs.length ? sum(runs.map((r) => s(r, k))) / runs.length : null);
  const byAgent = Object.entries(
    runs.reduce<Record<string, number>>((m, r) => {
      const a = r.agent_id ?? "unknown";
      m[a] = (m[a] ?? 0) + s(r, "estimated_cost_usd");
      return m;
    }, {}),
  ).sort((a, b) => b[1] - a[1]);
  const modelCost: Record<string, number> = {};
  for (const r of runs) {
    const models = Array.isArray(r.summary["models"]) ? (r.summary["models"] as string[]) : [];
    for (const m of models)
      modelCost[m] = (modelCost[m] ?? 0) + s(r, "estimated_cost_usd") / models.length;
  }
  const retryRuns = runs.filter((r) => s(r, "retry_count") > 0);
  const row = (r: RunOut) => ({
    run_id: r.id,
    project_id: r.project_id,
    name: r.name,
    agent: r.agent_id,
    status: r.status,
    started_at: r.started_at,
  });
  switch (kind) {
    case "summary":
      return {
        window,
        runs: counts,
        rates,
        cost: headline,
        run_latency: { count: durations.length, p50_ms: pct(0.5), p95_ms: pct(0.95) },
        behaviour: {
          avg_llm_calls: avg("llm_calls"),
          avg_tool_calls: avg("tool_calls"),
          avg_retries: avg("retry_count"),
          avg_files_modified: avg("files_modified"),
        },
        tools: { calls: 0, success_rate: null },
        llm: { calls: 0, success_rate: null },
        active_agents: [
          ...new Set(runs.filter((r) => isActive(r.status)).map((r) => r.agent_id ?? "")),
        ].filter(Boolean),
      } satisfies S["Summary"];
    case "cost":
      return {
        window,
        headline,
        retries: {
          total_usd: total,
          initial_usd: total - retry,
          retry_usd: retry,
          retry_share: headline.retry_share,
          runs_with_retry_cost: runs.filter((r) => retryUsd(r) > 0).length,
          retries_unattributed: 0,
        },
        by_day: days.map((d) => {
          const rs = runs.filter((r) => day(r) === d);
          return {
            day: d,
            cost_usd: sum(rs.map((r) => s(r, "estimated_cost_usd"))),
            retry_usd: sum(rs.map((r) => retryUsd(r))),
            runs: rs.length,
          };
        }),
        by_agent: byAgent.slice(0, top).map(([a, c]) => ({ agent: a, cost_usd: c, calls: 1 })),
        by_agent_other: null,
        by_model: Object.entries(modelCost)
          .sort((a, b) => b[1] - a[1])
          .slice(0, top)
          .map(([m, c]) => ({
            provider: null,
            model: m,
            cost_usd: c,
            calls: 1,
            input_tokens: 0,
            output_tokens: 0,
            unpriced_calls: 0,
          })),
        by_model_other: null,
        by_source: [{ source: "client_estimate", cost_usd: total, calls: 1 }],
        by_project: project ? null : [],
        expensive_runs: [...runs]
          .sort((a, b) => s(b, "estimated_cost_usd") - s(a, "estimated_cost_usd"))
          .filter((r) => s(r, "estimated_cost_usd") > 0)
          .slice(0, top)
          .map((r) => ({
            ...row(r),
            cost_usd: s(r, "estimated_cost_usd"),
            retry_usd: retryUsd(r),
            retry_count: s(r, "retry_count"),
          })),
      } satisfies S["CostReport"];
    case "reliability":
      return {
        window,
        runs: counts,
        rates,
        failure_trend: days.map((d) => {
          const rs = runs.filter((r) => day(r) === d && FINISHED.has(r.status));
          const c = (st: string) => rs.filter((r) => r.status === st).length;
          return {
            day: d,
            finished: rs.length,
            success: c("SUCCESS"),
            failed: c("FAILED"),
            timed_out: c("TIMED_OUT"),
            blocked: c("BLOCKED"),
            failure_rate: rate(c("FAILED") + c("BLOCKED"), rs.length),
            timeout_rate: rate(c("TIMED_OUT"), rs.length),
          };
        }),
        tools: [],
        tools_other: null,
        retry_heavy_runs: retryRuns
          .sort((a, b) => s(b, "retry_count") - s(a, "retry_count"))
          .slice(0, top)
          .map((r) => ({
            ...row(r),
            retry_count: s(r, "retry_count"),
            retry_usd: retryUsd(r),
            cost_usd: s(r, "estimated_cost_usd"),
          })),
      } satisfies S["ReliabilityReport"];
    case "performance":
      return {
        window,
        run: { count: durations.length, p50_ms: pct(0.5), p95_ms: pct(0.95) },
        llm: { count: 0, p50_ms: null, p95_ms: null },
        tool: { count: 0, p50_ms: null, p95_ms: null },
        slow_operations: [],
      } satisfies S["PerformanceReport"];
    default:
      return null;
  }
}
