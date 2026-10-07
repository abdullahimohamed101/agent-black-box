"use client";

import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./api/client";
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

/**
 * All events of a run, 500 per page, in canonical order. Pages load back-to-back so a 10,000-event run is complete
 * after ~20 requests while the first screen renders after one.
 */
export function useRunEvents(runId: string, eventCount: number | undefined) {
  return useInfiniteQuery({
    queryKey: ["run-events", runId, eventCount ?? 0],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }) =>
      unwrap(
        await api.GET("/v1/runs/{run_id}/events", {
          signal,
          params: { path: { run_id: runId }, query: { limit: 500, cursor: pageParam } },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    // A changed event_count is a new key (the run grew); finished data is immutable (INV-1).
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
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
