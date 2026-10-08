"use client";

import Link from "next/link";
import { useState } from "react";
import type { ExpensiveRun } from "@/lib/api/types";
import {
  usePerformanceReport,
  useCostReport,
  useReliabilityReport,
  WINDOW_OPTIONS,
  type WindowDays,
} from "@/lib/queries";
import { formatCost, formatDuration, formatInt, formatRelative } from "@/lib/format";
import { projectFilter, runPath, type Base } from "@/lib/routes";
import { BarList, DayColumns } from "./charts";
import { StatusBadge } from "./StatusBadge";
import { Empty, ErrorState, Loading } from "./States";

const pct = (r: number | null | undefined) => (r == null ? "—" : `${Math.round(r * 100)}%`);

function Section({
  id,
  title,
  query,
  children,
}: {
  id: string;
  title: string;
  query: { isPending: boolean; error: unknown; refetch: () => unknown };
  children: React.ReactNode;
}) {
  return (
    <section aria-labelledby={id}>
      <h2 id={id}>{title}</h2>
      {query.isPending ? (
        <Loading label={`Loading ${title.toLowerCase()}`} />
      ) : query.error ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : (
        children
      )}
    </section>
  );
}

function RunRows({
  runs,
  base,
  extra,
}: {
  runs: readonly ExpensiveRun[];
  base: Base;
  extra: "cost" | "retries";
}) {
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">
          {extra === "cost" ? "Most expensive runs" : "Runs with the most retries"}
        </caption>
        <thead>
          <tr>
            <th scope="col">Status</th>
            <th scope="col">Run</th>
            <th scope="col">Agent</th>
            <th scope="col">Started</th>
            <th scope="col" className="num">
              {extra === "cost" ? "Cost" : "Retries"}
            </th>
            <th scope="col" className="num">
              Retry cost
            </th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.run_id}>
              <td>
                <StatusBadge status={r.status as never} />
              </td>
              <td>
                <Link href={runPath(base, r.run_id)}>{r.name ?? r.run_id}</Link>
              </td>
              <td>{r.agent ?? "—"}</td>
              <td title={r.started_at}>{formatRelative(r.started_at)}</td>
              <td className="num">
                {extra === "cost" ? formatCost(r.cost_usd) : formatInt(r.retry_count)}
              </td>
              <td className="num">{formatCost(r.retry_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Analytics({ base }: { base: Base }) {
  const project = projectFilter(base.project);
  const [days, setDays] = useState<WindowDays>(7);
  const cost = useCostReport(project, days);
  const reliability = useReliabilityReport(project, days);
  const performance = usePerformanceReport(project, days);
  const c = cost.data;

  return (
    <>
      <h1>Analytics</h1>
      <div className="toolbar" role="group" aria-label="Time window">
        {WINDOW_OPTIONS.map((d) => (
          <button key={d} type="button" aria-pressed={d === days} onClick={() => setDays(d)}>
            {d === 1 ? "Today" : `Last ${d} days`}
          </button>
        ))}
      </div>
      <p className="muted">
        Windows are whole UTC days; a run counts in the day it started. Percentiles are approximate
        (about 10%). Cost is in USD; each figure says whether it was reported by the provider,
        computed from tokens and a versioned price, or estimated by the caller.
      </p>

      <Section id="cost-h" title="Cost" query={cost}>
        {c && c.headline.total_usd === 0 && c.by_model.length === 0 ? (
          <Empty title="No cost recorded in this window">
            Model calls with token usage or a cost appear here.
          </Empty>
        ) : (
          c && (
            <>
              <dl className="stats">
                <div className="stat">
                  <dt>Total</dt>
                  <dd>{formatCost(c.headline.total_usd)}</dd>
                </div>
                <div className="stat">
                  <dt>Per run</dt>
                  <dd>{formatCost(c.headline.per_run_usd)}</dd>
                </div>
                <div className="stat">
                  <dt>Per successful run</dt>
                  <dd>{formatCost(c.headline.per_successful_run_usd)}</dd>
                </div>
                <div className="stat">
                  <dt>Unpriced calls</dt>
                  <dd>{formatInt(c.headline.unpriced_calls)}</dd>
                </div>
              </dl>
              {c.headline.unrebuilt_runs > 0 && (
                <p role="note" className="muted">
                  {c.headline.unrebuilt_runs} runs predate cost calculation; breakdowns by model and
                  agent exclude them until their costs are rebuilt.
                </p>
              )}
              <h3>Daily spend</h3>
              <DayColumns
                caption="Daily spend"
                valueHeader="Cost (USD)"
                days={c.by_day.map((d) => ({
                  label: d.day,
                  value: d.cost_usd,
                  display: formatCost(d.cost_usd),
                }))}
              />
              <div className="two-col">
                <div>
                  <h3>By agent</h3>
                  <BarList
                    caption="Cost by agent"
                    bars={c.by_agent.map((a) => ({
                      label: a.agent,
                      value: a.cost_usd,
                      display: formatCost(a.cost_usd),
                      note: `${formatInt(a.calls)} calls`,
                    }))}
                  />
                  {c.by_agent_other && (
                    <p className="muted">
                      and {c.by_agent_other.groups} more agents:{" "}
                      {formatCost(c.by_agent_other.cost_usd)}
                    </p>
                  )}
                </div>
                <div>
                  <h3>By model</h3>
                  <BarList
                    caption="Cost by model"
                    bars={c.by_model.map((m) => ({
                      label: m.model ?? "(unknown model)",
                      value: m.cost_usd,
                      display: formatCost(m.cost_usd),
                      note: m.unpriced_calls
                        ? `${m.unpriced_calls} unpriced`
                        : `${formatInt(m.calls)} calls`,
                    }))}
                  />
                  {c.by_model_other && (
                    <p className="muted">
                      and {c.by_model_other.groups} more models:{" "}
                      {formatCost(c.by_model_other.cost_usd)}
                    </p>
                  )}
                </div>
              </div>
              <h3>Where the cost figures come from</h3>
              <BarList
                caption="Cost by source"
                bars={c.by_source.map((s) => ({
                  label: SOURCE_LABEL[s.source] ?? s.source,
                  value: s.cost_usd,
                  display: formatCost(s.cost_usd),
                  note: `${formatInt(s.calls)} calls`,
                }))}
              />
              <h3>Cost from retries</h3>
              {c.retries.retry_usd > 0 ? (
                <>
                  <p>
                    {c.retries.retry_share != null && (
                      <strong>{pct(c.retries.retry_share)} of cost came from retries. </strong>
                    )}
                    Initial attempts {formatCost(c.retries.initial_usd)}, retries{" "}
                    {formatCost(c.retries.retry_usd)} across {c.retries.runs_with_retry_cost} runs.
                  </p>
                </>
              ) : (
                <p className="muted">No model calls were made after a retry in this window.</p>
              )}
              {c.retries.retries_unattributed > 0 && (
                <p className="muted">
                  {c.retries.retries_unattributed} retries named no operation, so their cost cannot
                  be attributed.
                </p>
              )}
              <h3>Most expensive runs</h3>
              {c.expensive_runs.length ? (
                <RunRows runs={c.expensive_runs} base={base} extra="cost" />
              ) : (
                <p className="muted">No runs with cost.</p>
              )}
            </>
          )
        )}
      </Section>

      <Section id="rel-h" title="Reliability" query={reliability}>
        {reliability.data && (
          <>
            <dl className="stats">
              <div className="stat">
                <dt>Success rate</dt>
                <dd>{pct(reliability.data.rates.success_rate)}</dd>
              </div>
              <div className="stat">
                <dt>Failure rate</dt>
                <dd>{pct(reliability.data.rates.failure_rate)}</dd>
              </div>
              <div className="stat">
                <dt>Timeout rate</dt>
                <dd>{pct(reliability.data.rates.timeout_rate)}</dd>
              </div>
              <div className="stat">
                <dt>Retry rate</dt>
                <dd>{pct(reliability.data.rates.retry_rate)}</dd>
              </div>
            </dl>
            <h3>Failure trend</h3>
            <DayColumns
              caption="Share of finished runs that failed, timed out or were blocked"
              valueHeader="Failure rate"
              days={reliability.data.failure_trend.map((d) => ({
                label: d.day,
                value: d.failure_rate ?? 0,
                display: `${pct(d.failure_rate)} of ${d.finished}`,
                tone: "bad" as const,
              }))}
            />
            <h3>Tool success</h3>
            {reliability.data.tools.length ? (
              <div className="table-wrap">
                <table>
                  <caption className="sr-only">Tool success rate and latency</caption>
                  <thead>
                    <tr>
                      <th scope="col">Tool</th>
                      <th scope="col" className="num">
                        Calls
                      </th>
                      <th scope="col" className="num">
                        Success
                      </th>
                      <th scope="col" className="num">
                        p95
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {reliability.data.tools.map((t) => (
                      <tr key={t.name}>
                        <td>{t.name}</td>
                        <td className="num">{formatInt(t.calls)}</td>
                        <td className="num">{pct(t.success_rate)}</td>
                        <td className="num">{formatDuration(t.p95_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted">No tool calls in this window.</p>
            )}
            {reliability.data.tools_other && (
              <p className="muted">
                and {reliability.data.tools_other.groups} more tools (
                {formatInt(reliability.data.tools_other.calls)} calls)
              </p>
            )}
            <h3>Retry-heavy runs</h3>
            {reliability.data.retry_heavy_runs.length ? (
              <RunRows runs={reliability.data.retry_heavy_runs} base={base} extra="retries" />
            ) : (
              <p className="muted">No retries in this window.</p>
            )}
          </>
        )}
      </Section>

      <Section id="perf-h" title="Performance" query={performance}>
        {performance.data && (
          <>
            <dl className="stats">
              <div className="stat">
                <dt>Run latency p50 / p95</dt>
                <dd>
                  {formatDuration(performance.data.run.p50_ms)} /{" "}
                  {formatDuration(performance.data.run.p95_ms)}
                </dd>
              </div>
              <div className="stat">
                <dt>Model call p50 / p95</dt>
                <dd>
                  {formatDuration(performance.data.llm.p50_ms)} /{" "}
                  {formatDuration(performance.data.llm.p95_ms)}
                </dd>
              </div>
              <div className="stat">
                <dt>Tool call p50 / p95</dt>
                <dd>
                  {formatDuration(performance.data.tool.p50_ms)} /{" "}
                  {formatDuration(performance.data.tool.p95_ms)}
                </dd>
              </div>
            </dl>
            <h3>Slow operations</h3>
            {performance.data.slow_operations.length ? (
              <div className="table-wrap">
                <table>
                  <caption className="sr-only">Slowest operations by p95 duration</caption>
                  <thead>
                    <tr>
                      <th scope="col">Kind</th>
                      <th scope="col">Name</th>
                      <th scope="col" className="num">
                        Calls
                      </th>
                      <th scope="col" className="num">
                        p50
                      </th>
                      <th scope="col" className="num">
                        p95
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {performance.data.slow_operations.map((o) => (
                      <tr key={`${o.kind}/${o.name ?? ""}`}>
                        <td>{o.kind}</td>
                        <td>{o.name ?? "(all)"}</td>
                        <td className="num">{formatInt(o.calls)}</td>
                        <td className="num">{formatDuration(o.p50_ms)}</td>
                        <td className="num">{formatDuration(o.p95_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted">No timed operations in this window.</p>
            )}
          </>
        )}
      </Section>
    </>
  );
}

const SOURCE_LABEL: Record<string, string> = {
  provider_reported: "Reported by the provider",
  estimated: "Computed from tokens and prices",
  client_estimate: "Estimated by the caller",
  unpriced: "Unpriced (no usable cost)",
};
