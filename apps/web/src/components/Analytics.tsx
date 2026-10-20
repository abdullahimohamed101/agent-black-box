"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import {
  analytics as t,
  costHeadline,
  parseReport,
  performanceHeadline,
  reliabilityHeadline,
  REPORTS,
  type Report,
} from "@/copy/analytics";
import type { ExpensiveRun } from "@/lib/api/types";
import {
  usePerformanceReport,
  useCostReport,
  useReliabilityReport,
  WINDOW_OPTIONS,
  type WindowDays,
} from "@/lib/queries";
import { formatCost, formatDuration, formatInt, formatRelative } from "@/lib/format";
import { runPath, type Base } from "@/lib/routes";
import { useProjectId } from "@/components/WorkspaceProvider";
import { BarList, DayColumns } from "./charts";
import { Tabs, tabIds } from "./Tabs";
import { StatusBadge } from "./StatusBadge";
import { WastePanel } from "./WastePanel";
import { Empty, ErrorState, Loading } from "./States";
import "../app/analytics.css";

const pct = (r: number | null | undefined) => (r == null ? "—" : `${Math.round(r * 100)}%`);

/** Figures built from trace detail leave out runs whose trace the retention policy removed (ADR-161). Only when there are any. */
function ExpiredTraceNote({ runs, what }: { runs: number; what: string }) {
  if (runs <= 0) return null;
  return (
    <p role="note" className="notice" data-testid="expired-traces">
      {t.expired(runs, what)}
    </p>
  );
}

type Query = {
  isPending: boolean;
  isPlaceholderData?: boolean;
  error: unknown;
  refetch: () => unknown;
};

/** One report's body: loading, error (with a retry), or the figures, dimmed and announced while the next window loads. */
function ReportBody({
  label,
  query,
  children,
}: {
  label: string;
  query: Query;
  children: React.ReactNode;
}) {
  if (query.isPending) return <Loading label={label} />;
  if (query.error) return <ErrorState error={query.error} onRetry={() => void query.refetch()} />;
  return (
    <div
      aria-busy={query.isPlaceholderData ? "true" : undefined}
      data-stale={query.isPlaceholderData ? "true" : undefined}
    >
      {query.isPlaceholderData && (
        <p role="status" className="muted updating">
          {t.updating}
        </p>
      )}
      {children}
    </div>
  );
}

/** The sentence a report opens with: the one thing to read first, in ink. */
function Headline({ children }: { children: React.ReactNode }) {
  return (
    <p className="report-headline" data-testid="report-headline">
      {children}
    </p>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="an-block">
      <h2>{title}</h2>
      {children}
    </div>
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
  const h = t.runRows.heads;
  return (
    <div
      className="table-wrap"
      tabIndex={0}
      role="region"
      aria-label={extra === "cost" ? t.cost.expensiveCaption : t.reliability.retryHeavyCaption}
    >
      <table>
        <caption className="sr-only">
          {extra === "cost" ? t.cost.expensiveCaption : t.reliability.retryHeavyCaption}
        </caption>
        <thead>
          <tr>
            <th scope="col">{h.status}</th>
            <th scope="col">{h.run}</th>
            <th scope="col">{h.agent}</th>
            <th scope="col">{h.started}</th>
            <th scope="col" className="num">
              {t.runRows[extra]}
            </th>
            <th scope="col" className="num">
              {h.retryCost}
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

function CostReport({
  base,
  project,
  days,
  query,
}: {
  base: Base;
  project: string | null | undefined;
  days: WindowDays;
  query: ReturnType<typeof useCostReport>;
}) {
  const c = query.data;
  const k = t.cost;
  return (
    <ReportBody label={t.loading.cost} query={query}>
      {c && c.headline.total_usd === 0 && c.by_model.length === 0 ? (
        <Empty title={k.emptyTitle}>{k.emptyBody}</Empty>
      ) : (
        c && (
          <>
            <Headline>{costHeadline(c.headline, days)}</Headline>
            {c.headline.unrebuilt_runs > 0 && (
              <p role="note" className="notice">
                {k.unrebuilt(c.headline.unrebuilt_runs)}
              </p>
            )}
            <ExpiredTraceNote runs={c.excluded_expired_traces} what={t.expiredWhat.cost} />
            <WastePanel project={project} days={days} />
            <Block title={k.daily}>
              <DayColumns
                caption={k.daily}
                valueHeader={k.dailyValue}
                days={c.by_day.map((d) => ({
                  label: d.day,
                  value: d.cost_usd,
                  display: formatCost(d.cost_usd),
                }))}
              />
            </Block>
            <div className="two-col">
              <Block title={k.byAgent}>
                <BarList
                  caption="Cost by agent"
                  bars={c.by_agent.map((a) => ({
                    label: a.agent,
                    value: a.cost_usd,
                    display: formatCost(a.cost_usd),
                    note: k.calls(a.calls),
                  }))}
                />
                {c.by_agent_other && (
                  <p className="muted">
                    {t.other(
                      c.by_agent_other.groups,
                      "agents",
                      formatCost(c.by_agent_other.cost_usd),
                    )}
                  </p>
                )}
              </Block>
              <Block title={k.byModel}>
                <BarList
                  caption="Cost by model"
                  bars={c.by_model.map((m) => ({
                    label: m.model ?? k.unknownModel,
                    value: m.cost_usd,
                    display: formatCost(m.cost_usd),
                    note: m.unpriced_calls ? k.unpriced(m.unpriced_calls) : k.calls(m.calls),
                  }))}
                />
                {c.by_model_other && (
                  <p className="muted">
                    {t.other(
                      c.by_model_other.groups,
                      "models",
                      formatCost(c.by_model_other.cost_usd),
                    )}
                  </p>
                )}
              </Block>
            </div>
            <Block title={k.sources}>
              <BarList
                caption="Cost by source"
                bars={c.by_source.map((s) => ({
                  label: k.sourceLabel[s.source] ?? s.source,
                  value: s.cost_usd,
                  display: formatCost(s.cost_usd),
                  note: k.calls(s.calls),
                }))}
              />
            </Block>
            <Block title={k.retries}>
              <p className="muted">{k.retriesBasis}</p>
              {c.retries.retry_usd > 0 ? (
                <p>
                  {c.retries.retry_share != null && (
                    <strong>{k.retriesShare(pct(c.retries.retry_share))} </strong>
                  )}
                  {k.retriesFigures(
                    formatCost(c.retries.initial_usd),
                    formatCost(c.retries.retry_usd),
                    c.retries.runs_with_retry_cost,
                  )}
                </p>
              ) : (
                <p className="muted">{k.retriesNone}</p>
              )}
              {c.retries.retries_unattributed > 0 && (
                <p className="muted">{k.retriesUnattributed(c.retries.retries_unattributed)}</p>
              )}
            </Block>
            <Block title={k.expensive}>
              {c.expensive_runs.length ? (
                <RunRows runs={c.expensive_runs} base={base} extra="cost" />
              ) : (
                <p className="muted">{k.expensiveNone}</p>
              )}
            </Block>
          </>
        )
      )}
    </ReportBody>
  );
}

function ReliabilityReport({
  base,
  days,
  query,
}: {
  base: Base;
  days: WindowDays;
  query: ReturnType<typeof useReliabilityReport>;
}) {
  const r = query.data;
  const k = t.reliability;
  return (
    <ReportBody label={t.loading.reliability} query={query}>
      {r && (
        <>
          <Headline>{reliabilityHeadline(r.rates, days)}</Headline>
          <ExpiredTraceNote runs={r.excluded_expired_traces} what={t.expiredWhat.reliability} />
          <Block title={k.trend}>
            <DayColumns
              caption={k.trendCaption}
              valueHeader={k.trendValue}
              days={r.failure_trend.map((d) => {
                const rate = (d.failure_rate ?? 0) + (d.timeout_rate ?? 0);
                return {
                  label: d.day,
                  value: rate,
                  display: k.trendOf(pct(rate), d.finished),
                };
              })}
              peak={k.trendPeak}
            />
          </Block>
          <Block title={k.tools}>
            {r.tools.length ? (
              <div className="table-wrap" tabIndex={0} role="region" aria-label={k.toolsCaption}>
                <table>
                  <caption className="sr-only">{k.toolsCaption}</caption>
                  <thead>
                    <tr>
                      <th scope="col">{k.heads.tool}</th>
                      <th scope="col" className="num">
                        {k.heads.calls}
                      </th>
                      <th scope="col" className="num">
                        {k.heads.success}
                      </th>
                      <th scope="col" className="num">
                        {k.heads.p95}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.tools.map((tool) => (
                      <tr key={tool.name}>
                        <td>{tool.name}</td>
                        <td className="num">{formatInt(tool.calls)}</td>
                        <td className="num">{pct(tool.success_rate)}</td>
                        <td className="num">{formatDuration(tool.p95_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted">{k.toolsNone}</p>
            )}
            {r.tools_other && (
              <p className="muted">{k.toolsOther(r.tools_other.groups, r.tools_other.calls)}</p>
            )}
          </Block>
          <Block title={k.retryHeavy}>
            {r.retry_heavy_runs.length ? (
              <RunRows runs={r.retry_heavy_runs} base={base} extra="retries" />
            ) : (
              <p className="muted">{k.retryHeavyNone}</p>
            )}
          </Block>
        </>
      )}
    </ReportBody>
  );
}

function PerformanceReport({
  days,
  query,
}: {
  days: WindowDays;
  query: ReturnType<typeof usePerformanceReport>;
}) {
  const p = query.data;
  const k = t.performance;
  return (
    <ReportBody label={t.loading.performance} query={query}>
      {p && (
        <>
          <Headline>{performanceHeadline(p.run, days)}</Headline>
          <ExpiredTraceNote runs={p.excluded_expired_traces} what={t.expiredWhat.performance} />
          <Block title={k.latency}>
            <div className="table-wrap" tabIndex={0} role="region" aria-label={k.latencyCaption}>
              <table>
                <caption className="sr-only">{k.latencyCaption}</caption>
                <thead>
                  <tr>
                    <th scope="col">{k.latencyHeads.kind}</th>
                    <th scope="col" className="num">
                      {k.latencyHeads.p50}
                    </th>
                    <th scope="col" className="num">
                      {k.latencyHeads.p95}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {(["run", "llm", "tool"] as const).map((kind) => (
                    <tr key={kind}>
                      <td>{k.latencyRows[kind]}</td>
                      <td className="num">{formatDuration(p[kind].p50_ms)}</td>
                      <td className="num">{formatDuration(p[kind].p95_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Block>
          <Block title={k.slow}>
            {p.slow_operations.length ? (
              <div className="table-wrap" tabIndex={0} role="region" aria-label={k.slowCaption}>
                <table>
                  <caption className="sr-only">{k.slowCaption}</caption>
                  <thead>
                    <tr>
                      <th scope="col">{k.slowHeads.kind}</th>
                      <th scope="col">{k.slowHeads.name}</th>
                      <th scope="col" className="num">
                        {k.slowHeads.calls}
                      </th>
                      <th scope="col" className="num">
                        {k.slowHeads.p50}
                      </th>
                      <th scope="col" className="num">
                        {k.slowHeads.p95}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {p.slow_operations.map((o) => (
                      <tr key={`${o.kind}/${o.name ?? ""}`}>
                        <td>{o.kind}</td>
                        <td>{o.name ?? k.allNames}</td>
                        <td className="num">{formatInt(o.calls)}</td>
                        <td className="num">{formatDuration(o.p50_ms)}</td>
                        <td className="num">{formatDuration(o.p95_ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted">{k.slowNone}</p>
            )}
          </Block>
        </>
      )}
    </ReportBody>
  );
}

/**
 * Analytics (design pass 2 step 5d): the title line with the one control row (the window), the shared tab list of the three
 * reports, and a panel that opens with one sentence in ink. All three reports are requested at once, as before, so a switch of
 * tab is instant and one failing report never hides the others. The tab is the URL's `?tab=` (Cost when absent).
 */
export function Analytics({ base }: { base: Base }) {
  const project = useProjectId(base.project);
  const [days, setDays] = useState<WindowDays>(7);
  const sp = useSearchParams();
  const [tab, setTab] = useState<Report>(() => parseReport(sp?.get("tab")));
  const cost = useCostReport(project, days);
  const reliability = useReliabilityReport(project, days);
  const performance = usePerformanceReport(project, days);

  const choose = (next: Report) => {
    setTab(next);
    const url = new URL(window.location.href);
    if (next === "cost") url.searchParams.delete("tab");
    else url.searchParams.set("tab", next);
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);
  };

  return (
    <div className="analytics">
      <div className="an-head">
        <h1>{t.title}</h1>
        <div className="toolbar" role="group" aria-label={t.windowLabel}>
          {WINDOW_OPTIONS.map((d) => (
            <button key={d} type="button" aria-pressed={d === days} onClick={() => setDays(d)}>
              {t.windowOption(d)}
            </button>
          ))}
        </div>
      </div>
      <Tabs
        label={t.tabsLabel}
        idBase="analytics"
        className="tabs-ruled"
        items={REPORTS.map((r) => ({ id: r, label: t.tabs[r] }))}
        value={tab}
        onChange={choose}
      />
      <div role="tabpanel" className="an-panel" {...tabIds("analytics", tab)}>
        {tab === "cost" && <CostReport base={base} project={project} days={days} query={cost} />}
        {tab === "reliability" && <ReliabilityReport base={base} days={days} query={reliability} />}
        {tab === "performance" && <PerformanceReport days={days} query={performance} />}
      </div>
      <p className="muted an-foot">{t.basis}</p>
    </div>
  );
}
