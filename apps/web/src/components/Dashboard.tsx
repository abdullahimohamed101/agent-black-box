"use client";

import Link from "next/link";
import { formatCost, formatDuration } from "@/lib/format";
import { analyticsPath, runsPath, projectFilter, type Base } from "@/lib/routes";
import { useAnalyticsSummary, useRuns } from "@/lib/queries";
import { RunsTable } from "./RunsTable";
import { Empty, ErrorState, Loading } from "./States";

const DAYS = 7;

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="stat">
      <dt>{label}</dt>
      <dd>
        {value}
        {hint && <small className="muted">{hint}</small>}
      </dd>
    </div>
  );
}

const pct = (r: number | null) => (r == null ? "—" : `${Math.round(r * 100)}%`);

/**
 * Figures come from the server's aggregate endpoint over the whole window (KI-028 resolved); the two run tables
 * are the newest runs, fetched separately.
 */
export function Dashboard({ base }: { base: Base }) {
  const project = projectFilter(base.project);
  const stats = useAnalyticsSummary(project, DAYS);
  const recent = useRuns({ project }, 8);
  const failed = useRuns({ project, statuses: ["FAILED", "TIMED_OUT", "BLOCKED"] }, 5);

  if (stats.isPending || recent.isPending) return <Loading label="Loading dashboard" />;
  const error = stats.error ?? recent.error;
  if (error) {
    return (
      <ErrorState
        error={error}
        onRetry={() => {
          void stats.refetch();
          void recent.refetch();
        }}
      />
    );
  }
  const d = stats.data!;
  const recentRuns = recent.data?.pages[0]?.items ?? [];
  if (d.runs.total === 0 && recentRuns.length === 0) {
    return (
      <Empty title="No runs yet">
        Instrument an agent with the SDK or send events to the API; runs appear here within a few
        seconds.
      </Empty>
    );
  }
  const failures = failed.data?.pages[0]?.items ?? [];
  const stale = stats.isPlaceholderData;
  return (
    <div aria-busy={stale ? "true" : undefined} data-stale={stale ? "true" : undefined}>
      {stale && (
        <p role="status" className="muted updating">
          Updating…
        </p>
      )}
      <h1>Dashboard</h1>
      <p className="muted">
        All {d.runs.total.toLocaleString("en-US")} runs started in the last {DAYS} days.{" "}
        <Link href={analyticsPath(base)}>Cost and reliability analytics →</Link>
      </p>
      <dl className="stats">
        <Stat
          label="Success rate"
          value={pct(d.rates.success_rate)}
          hint={`${d.runs.success} of ${d.runs.finished} finished runs`}
        />
        <Stat
          label="Failures"
          value={String(d.runs.failed + d.runs.timed_out + d.runs.blocked)}
          hint="failed, timed out or blocked"
        />
        <Stat
          label="Cost"
          value={formatCost(d.cost.total_usd)}
          hint={
            d.cost.unpriced_calls
              ? `${d.cost.unpriced_calls} model calls unpriced`
              : "computed from tokens and prices"
          }
        />
        <Stat
          label="Median duration"
          value={formatDuration(d.run_latency.p50_ms)}
          hint="finished runs"
        />
        <Stat
          label="Running agents"
          value={String(d.active_agents.length)}
          hint={
            d.active_agents.length ? d.active_agents.join(", ") : `${d.runs.active} active runs`
          }
        />
      </dl>

      {failures.length > 0 && (
        <section aria-labelledby="fail-h">
          <h2 id="fail-h">Recent failures</h2>
          <RunsTable runs={failures} base={base} caption="Recent failed runs" />
        </section>
      )}
      <section aria-labelledby="recent-h">
        <h2 id="recent-h">Recent runs</h2>
        <RunsTable runs={recentRuns} base={base} caption="Recent runs" />
        <p>
          <Link href={runsPath(base)}>All runs →</Link>
        </p>
      </section>
    </div>
  );
}
