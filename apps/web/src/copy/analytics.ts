import { formatCost, formatDuration, formatInt } from "@/lib/format";

/** Strings for Analytics (docs/design/pass-2/05-microcopy-and-states.md section 10). Numbers carry their units. */

export const REPORTS = ["cost", "reliability", "performance"] as const;
export type Report = (typeof REPORTS)[number];
export const parseReport = (v: string | null | undefined): Report =>
  REPORTS.find((r) => r === v) ?? "cost";

/** A rate that rounds to 0% is left out of a sentence. */
const shown = (r: number | null | undefined) => r != null && Math.round(r * 100) > 0;
const pct = (r: number | null | undefined) => (r == null ? null : `${Math.round(r * 100)}%`);

/** "today" or "in the last 7 days": the window in words, for the headline sentences. */
export const windowPhrase = (days: number): string =>
  days === 1 ? "today" : `in the last ${days} days`;

export const analytics = {
  title: "Analytics",
  windowLabel: "Time window",
  windowOption: (days: number) => (days === 1 ? "Today" : `Last ${days} days`),
  tabsLabel: "Reports",
  tabs: { cost: "Cost", reliability: "Reliability", performance: "Performance" },
  /** Under every report, once: how the figures are counted. Small, ink-2. */
  basis:
    "Windows are whole UTC days; a run counts in the day it started. Percentiles are approximate (about 10%). Cost is in USD; each figure says whether it was reported by the provider, computed from tokens and a versioned price, or estimated by the caller.",
  updating: "Updating…",
  noData: "No data in this window.",
  other: (n: number, what: "agents" | "models", cost: string) => `and ${n} more ${what}: ${cost}`,
  loading: {
    cost: "Loading cost",
    reliability: "Loading reliability",
    performance: "Loading performance",
  },
  /** Retention (ADR-161): figures built from trace detail leave out runs whose trace has expired. */
  expired: (runs: number, what: string) =>
    `Excludes ${formatInt(runs)} run${runs === 1 ? "" : "s"} whose trace has expired: ${what} need the trace detail. Run counts, totals and the cost headline include them.`,
  expiredWhat: {
    cost: "the cost by agent, model and source",
    reliability: "the tool success rates below",
    performance: "the model and tool latencies and the slow operations",
  },
  cost: {
    emptyTitle: "No cost recorded in this window",
    emptyBody: "Model calls with token usage or a cost appear here.",
    unrebuilt: (n: number) =>
      `${n} runs were summarised by an older version; per-agent figures, and cost breakdowns for runs that predate pricing, may be missing until their costs are rebuilt.`,
    daily: "Daily spend",
    dailyValue: "Cost (USD)",
    byAgent: "By agent",
    byModel: "By model",
    unknownModel: "(unknown model)",
    calls: (n: number) => `${formatInt(n)} call${n === 1 ? "" : "s"}`,
    unpriced: (n: number) => `${n} unpriced`,
    sources: "Where the cost figures come from",
    sourceLabel: {
      provider_reported: "Reported by the provider",
      estimated: "Computed from tokens and prices",
      client_estimate: "Estimated by the caller",
      unpriced: "Unpriced (no usable cost)",
    } as Record<string, string>,
    retries: "Cost from retries",
    retriesBasis:
      "Estimated: every model call made after a retry of the same operation counts as retry cost.",
    retriesShare: (share: string) => `${share} of cost came from retries.`,
    retriesFigures: (initial: string, retry: string, runs: number) =>
      `Initial attempts ${initial}, retries ${retry} across ${runs} runs.`,
    retriesNone: "No model calls were made after a retry in this window.",
    retriesUnattributed: (n: number) =>
      `${n} retries named no operation, so their cost cannot be attributed.`,
    expensive: "Most expensive runs",
    expensiveCaption: "Most expensive runs",
    expensiveNone: "No runs with cost.",
  },
  reliability: {
    trend: "Failure trend",
    trendCaption: "Share of finished runs that failed, timed out or were blocked",
    trendValue: "Failure rate",
    trendOf: (rate: string, finished: number) => `${rate} of ${finished}`,
    trendPeak: (display: string, label: string) => `Highest: ${display} on ${label}`,
    tools: "Tool success",
    toolsCaption: "Tool success rate and latency",
    toolsNone: "No tool calls in this window.",
    toolsOther: (groups: number, calls: number) =>
      `and ${groups} more tools (${formatInt(calls)} calls)`,
    retryHeavy: "Retry-heavy runs",
    retryHeavyCaption: "Runs with the most retries",
    retryHeavyNone: "No retries in this window.",
    heads: { tool: "Tool", calls: "Calls", success: "Success", p95: "p95" },
  },
  performance: {
    latency: "Latency",
    latencyCaption: "Latency by kind of work",
    latencyHeads: { kind: "Work", p50: "p50", p95: "p95" },
    latencyRows: { run: "Run", llm: "Model call", tool: "Tool call" },
    slow: "Slow operations",
    slowCaption: "Slowest operations by p95 duration",
    slowNone: "No timed operations in this window.",
    slowHeads: { kind: "Kind", name: "Name", calls: "Calls", p50: "p50", p95: "p95" },
    allNames: "(all)",
  },
  runRows: {
    cost: "Cost",
    retries: "Retries",
    heads: {
      status: "Status",
      run: "Run",
      agent: "Agent",
      started: "Started",
      retryCost: "Retry cost",
    },
  },
  waste: {
    title: "Where the money was wasted",
    loading: "Loading wasted spend",
    emptyTitle: "Nothing was wasted in this window",
    emptyBody: "No run failed or retried with a cost.",
    byAgent: "Wasted by agent",
    byModel: "Wasted by model",
    basis:
      "Spend on runs that failed, timed out or were blocked, plus retries in the runs that did not. Retries inside a failed run are already in its spend.",
  },
} as const;

type Headline = {
  total_usd: number;
  per_run_usd: number | null;
  per_successful_run_usd: number | null;
  unpriced_calls: number;
};
/**
 * The Cost report's one sentence, from the server's headline only (nothing is recomputed in the browser):
 * "$8.00 spent in the last 7 days, $2.00 per run and $2.25 per successful run."
 */
export function costHeadline(h: Headline, days: number): string {
  const per = [
    h.per_run_usd == null ? null : `${formatCost(h.per_run_usd)} per run`,
    h.per_successful_run_usd == null
      ? null
      : `${formatCost(h.per_successful_run_usd)} per successful run`,
  ].filter((x): x is string => x !== null);
  const lead = `${formatCost(h.total_usd)} spent ${windowPhrase(days)}`;
  const body = per.length ? `${lead}, ${per.join(" and ")}.` : `${lead}.`;
  return h.unpriced_calls > 0
    ? `${body} ${formatInt(h.unpriced_calls)} model call${h.unpriced_calls === 1 ? " has no price and counts" : "s have no price and count"} as $0.`
    : body;
}

type Rates = {
  success_rate: number | null;
  failure_rate: number | null;
  timeout_rate: number | null;
  retry_rate: number | null;
};
/** "50% of finished runs succeeded in the last 7 days; 25% failed and 25% timed out. 10% had at least one retry." A rate of zero is left out. */
export function reliabilityHeadline(r: Rates, days: number): string {
  const when = windowPhrase(days);
  const success = pct(r.success_rate);
  if (success === null) return `No runs finished ${when}.`;
  const bad = [
    shown(r.failure_rate) && `${pct(r.failure_rate)} failed`,
    shown(r.timeout_rate) && `${pct(r.timeout_rate)} timed out`,
  ].filter((x): x is string => !!x);
  const head = `${success} of finished runs succeeded ${when}`;
  const mid = bad.length ? `; ${bad.join(" and ")}.` : "; none failed or timed out.";
  const retry = shown(r.retry_rate) ? pct(r.retry_rate) : null;
  return retry ? `${head}${mid} ${retry} had at least one retry.` : `${head}${mid}`;
}

/** "Half of runs finished within 5.1 s and 95% within 12 s in the last 7 days." */
export function performanceHeadline(
  run: { p50_ms: number | null; p95_ms: number | null },
  days: number,
): string {
  if (run.p50_ms == null || run.p95_ms == null) return `No timed runs ${windowPhrase(days)}.`;
  return `Half of runs finished within ${formatDuration(run.p50_ms)} and 95% within ${formatDuration(run.p95_ms)} ${windowPhrase(days)}.`;
}
