"use client";

import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api, ApiRequestError, unwrap } from "./api/client";
import { isActive, type RunStatus } from "./api/types";

export type RunFilters = {
  project?: string | null;
  statuses?: readonly RunStatus[];
  agent?: string;
  startedAfter?: string;
  sort?: "-started_at" | "started_at";
};

const POLL_MS = 3000;
export const runsKey = (f: RunFilters) => ["runs", f] as const;

export function useRuns(f: RunFilters, limit = 50) {
  return useInfiniteQuery({
    queryKey: [...runsKey(f), limit],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }) =>
      unwrap(
        await api.GET("/v1/runs", {
          signal,
          params: {
            query: {
              limit,
              cursor: pageParam,
              project_id: f.project || undefined,
              status: f.statuses?.length ? [...f.statuses] : undefined,
              agent_id: f.agent || undefined,
              started_after: f.startedAfter,
              sort: f.sort,
            },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: 5000,
    refetchInterval: POLL_MS * 5,
    placeholderData: keepPreviousData,
  });
}

export function useRun(runId: string) {
  return useQuery({
    queryKey: ["run", runId],
    queryFn: async ({ signal }) =>
      unwrap(await api.GET("/v1/runs/{run_id}", { signal, params: { path: { run_id: runId } } })),
    // Active runs poll until Phase 5 replaces this with SSE (ADR-020).
    refetchInterval: (q) => (q.state.data && isActive(q.state.data.status) ? POLL_MS : false),
  });
}

const MAX_STALE_RESETS = 3;

/**
 * All events of a run, 500 per page, in canonical order. Pages load back-to-back so a 10,000-event run is complete
 * after ~20 requests while the first screen renders after one.
 *
 * Interim live behaviour (ADR-020, replaced by SSE in Phase 5): the key does not include the event count. A finished
 * run's events are immutable (INV-1) and never refetched; an active run is refetched by the caller only when its
 * event_count or status changes. A `409 CURSOR_STALE` (ordering mode changed mid-paging) resets the query so paging
 * restarts from page 1, at most MAX_STALE_RESETS times.
 */
export function useRunEvents(runId: string, active: boolean) {
  const client = useQueryClient();
  const resets = useRef(0);
  const q = useInfiniteQuery({
    queryKey: ["run-events", runId],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }) =>
      unwrap(
        await api.GET("/v1/runs/{run_id}/events", {
          signal,
          params: { path: { run_id: runId }, query: { limit: 500, cursor: pageParam } },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: active ? 0 : Infinity,
    refetchOnMount: active ? "always" : false,
    placeholderData: keepPreviousData,
  });
  const err = q.error;
  const refetch = q.refetch;
  useEffect(() => {
    if (
      err instanceof ApiRequestError &&
      err.code === "CURSOR_STALE" &&
      resets.current < MAX_STALE_RESETS
    ) {
      resets.current += 1;
      // Drop the partial pages, then page again from the start.
      client.removeQueries({ queryKey: ["run-events", runId] });
      void refetch();
    }
  }, [err, client, runId, refetch]);
  return q;
}

export function useEventDetail(runId: string, eventId: string | null) {
  return useQuery({
    enabled: eventId != null,
    queryKey: ["event", runId, eventId],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/runs/{run_id}/events/{event_id}", {
          signal,
          params: { path: { run_id: runId, event_id: eventId! } },
        }),
      ),
    staleTime: Infinity,
  });
}

/** Analytics windows are whole UTC days (the server snaps them): "7 days" is today and the six before it. */
export type WindowDays = 1 | 7 | 30;
export const WINDOW_OPTIONS: readonly WindowDays[] = [1, 7, 30];
const DAY = 86_400_000;
export function windowFrom(days: WindowDays, now: number = Date.now()): string {
  return new Date((Math.floor(now / DAY) - (days - 1)) * DAY).toISOString();
}

function analyticsQuery(project: string | null | undefined, days: WindowDays) {
  return { project_id: project || undefined, from: windowFrom(days) };
}

const ANALYTICS_STALE_MS = 30_000;

export function useAnalyticsSummary(project: string | null | undefined, days: WindowDays) {
  return useQuery({
    queryKey: ["analytics", "summary", project ?? null, days],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/analytics/summary", {
          signal,
          params: { query: analyticsQuery(project, days) },
        }),
      ),
    staleTime: ANALYTICS_STALE_MS,
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  });
}

export function useCostReport(project: string | null | undefined, days: WindowDays) {
  return useQuery({
    queryKey: ["analytics", "cost", project ?? null, days],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/analytics/cost", {
          signal,
          params: { query: analyticsQuery(project, days) },
        }),
      ),
    staleTime: ANALYTICS_STALE_MS,
    placeholderData: keepPreviousData,
  });
}

export function useReliabilityReport(project: string | null | undefined, days: WindowDays) {
  return useQuery({
    queryKey: ["analytics", "reliability", project ?? null, days],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/analytics/reliability", {
          signal,
          params: { query: analyticsQuery(project, days) },
        }),
      ),
    staleTime: ANALYTICS_STALE_MS,
    placeholderData: keepPreviousData,
  });
}

export function usePerformanceReport(project: string | null | undefined, days: WindowDays) {
  return useQuery({
    queryKey: ["analytics", "performance", project ?? null, days],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/analytics/performance", {
          signal,
          params: { query: analyticsQuery(project, days) },
        }),
      ),
    staleTime: ANALYTICS_STALE_MS,
    placeholderData: keepPreviousData,
  });
}
