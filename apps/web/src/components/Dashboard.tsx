"use client";

import Link from "next/link";
import { useMemo } from "react";
import { computeDashboard } from "@/lib/dashboard";
import { formatCost, formatDuration } from "@/lib/format";
import { runsPath, projectFilter, type Base } from "@/lib/routes";
import { useRuns } from "@/lib/queries";
import { RunsTable } from "./RunsTable";
import { Empty, ErrorState, Loading } from "./States";

const SAMPLE = 200;

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

export function Dashboard({ base }: { base: Base }) {
  const q = useRuns({ project: projectFilter(base.project) }, SAMPLE);
  const runs = useMemo(() => q.data?.pages[0]?.items ?? [], [q.data]);
  const d = useMemo(() => computeDashboard(runs), [runs]);

  if (q.isPending) return <Loading label="Loading dashboard" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  if (!runs.length) {
    return (
      <Empty title="No runs yet">
        Instrument an agent with the SDK or send events to the API; runs appear here within a few
        seconds.
      </Empty>
    );
  }
  const more = q.data?.pages[0]?.next_cursor != null;
  return (
    <>
      <h1>Dashboard</h1>
      <p className="muted">
        Based on the latest {d.sampleSize}
        {more ? "+" : ""} runs{more ? " (older runs are not included)" : ""}.
      </p>
      <dl className="stats">
        <Stat
          label="Success rate"
          value={d.successRate == null ? "—" : `${Math.round(d.successRate * 100)}%`}
          hint={`${d.succeeded} of ${d.finished} finished runs`}
        />
        <Stat label="Failures" value={String(d.failures)} hint="failed, timed out or blocked" />
        <Stat label="Cost" value={formatCost(d.totalCostUsd)} hint="event-reported estimate" />
        <Stat label="Avg duration" value={formatDuration(d.avgDurationMs)} hint="finished runs" />
        <Stat
          label="Running agents"
          value={String(d.activeAgents.length)}
          hint={d.activeAgents.length ? d.activeAgents.join(", ") : `${d.activeRuns} active runs`}
        />
      </dl>

      {d.recentFailures.length > 0 && (
        <section aria-labelledby="fail-h">
          <h2 id="fail-h">Recent failures</h2>
          <RunsTable runs={d.recentFailures} base={base} caption="Recent failed runs" />
        </section>
      )}
      <section aria-labelledby="recent-h">
        <h2 id="recent-h">Recent runs</h2>
        <RunsTable runs={d.recent} base={base} caption="Recent runs" />
        <p>
          <Link href={runsPath(base)}>All runs →</Link>
        </p>
      </section>
    </>
  );
}
