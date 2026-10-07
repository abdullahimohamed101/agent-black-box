"use client";

import { useMemo, useState } from "react";
import { ALL_STATUSES, type RunStatus } from "@/lib/api/types";
import { useRuns } from "@/lib/queries";
import type { Base } from "@/lib/routes";
import { projectFilter } from "@/lib/routes";
import { RunsTable } from "./RunsTable";
import { statusLabel } from "./StatusBadge";
import { Empty, ErrorState, Loading } from "./States";

export type RangeKey = "1h" | "24h" | "7d" | "all";
export type ListFilters = { statuses: RunStatus[]; agent: string; range: RangeKey };
export const DEFAULT_FILTERS: ListFilters = { statuses: [], agent: "", range: "all" };
const RANGE_MS: Record<RangeKey, number | null> = {
  "1h": 3.6e6,
  "24h": 8.64e7,
  "7d": 6.048e8,
  all: null,
};
const RANGE_LABEL: Record<RangeKey, string> = {
  "1h": "Last hour",
  "24h": "Last 24 hours",
  "7d": "Last 7 days",
  all: "All time",
};
const AGENT_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/;

export function RunsList({
  base,
  filters,
  onFilters,
  now: nowProp,
  pageSize = 50,
}: {
  base: Base;
  filters: ListFilters;
  onFilters: (f: ListFilters) => void;
  now?: number;
  pageSize?: number;
}) {
  // Fixed at mount: a clock in the query key would refetch on every render.
  const [mounted] = useState(() => Date.now());
  const now = nowProp ?? mounted;
  // A half-typed agent slug would be a 422 from the API; only send valid ones.
  const agent = AGENT_RE.test(filters.agent) ? filters.agent : "";
  const span = RANGE_MS[filters.range];
  const startedAfter = useMemo(
    () =>
      span == null ? undefined : new Date(Math.floor((now - span) / 60_000) * 60_000).toISOString(),
    [span, now],
  );
  const q = useRuns(
    {
      project: projectFilter(base.project),
      statuses: filters.statuses,
      agent,
      startedAfter,
    },
    pageSize,
  );
  const runs = q.data?.pages.flatMap((p) => p.items) ?? [];
  const filtered = filters.statuses.length > 0 || filters.agent !== "" || filters.range !== "all";

  const toggle = (s: RunStatus) =>
    onFilters({
      ...filters,
      statuses: filters.statuses.includes(s)
        ? filters.statuses.filter((x) => x !== s)
        : [...filters.statuses, s],
    });

  return (
    <>
      <h1>Runs</h1>
      <form
        className="filters"
        role="search"
        aria-label="Filter runs"
        onSubmit={(e) => e.preventDefault()}
      >
        <fieldset>
          <legend>Status</legend>
          {ALL_STATUSES.map((s) => (
            <label key={s} className="chip">
              <input
                type="checkbox"
                checked={filters.statuses.includes(s)}
                onChange={() => toggle(s)}
              />
              {statusLabel(s)}
            </label>
          ))}
        </fieldset>
        <label>
          Agent
          <input
            type="text"
            value={filters.agent}
            placeholder="e.g. coding-agent"
            aria-invalid={filters.agent !== "" && agent === ""}
            onChange={(e) => onFilters({ ...filters, agent: e.target.value.trim() })}
          />
        </label>
        <label>
          Started
          <select
            value={filters.range}
            onChange={(e) => onFilters({ ...filters, range: e.target.value as RangeKey })}
          >
            {(Object.keys(RANGE_LABEL) as RangeKey[]).map((k) => (
              <option key={k} value={k}>
                {RANGE_LABEL[k]}
              </option>
            ))}
          </select>
        </label>
        {filtered && (
          <button type="button" onClick={() => onFilters(DEFAULT_FILTERS)}>
            Clear filters
          </button>
        )}
      </form>

      {q.isPending ? (
        <Loading label="Loading runs" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : runs.length === 0 ? (
        <Empty title={filtered ? "No runs match these filters" : "No runs yet"}>
          {filtered ? "Try clearing a filter." : "Runs appear here once an agent sends events."}
        </Empty>
      ) : (
        <>
          <RunsTable runs={runs} base={base} caption="Runs" now={now} />
          <p className="muted" aria-live="polite">
            {runs.length} run{runs.length === 1 ? "" : "s"} loaded
            {q.hasNextPage ? "" : " (end of list)"}
          </p>
          {q.hasNextPage && (
            <button
              type="button"
              disabled={q.isFetchingNextPage}
              onClick={() => void q.fetchNextPage()}
            >
              {q.isFetchingNextPage ? "Loading…" : "Load more"}
            </button>
          )}
        </>
      )}
    </>
  );
}
