"use client";

import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
} from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api, ApiRequestError, unwrap } from "./api/client";
import {
  LIVE_EVALUATION_STATES,
  isActive,
  type EvaluatorIn,
  type ExperimentIn,
  type RunStatus,
  type VariantIn,
  type VerdictIn,
} from "./api/types";

export type RunFilters = {
  project?: string | null;
  statuses?: readonly RunStatus[];
  agent?: string;
  startedAfter?: string;
  /** The search text (spec §152), sent verbatim; the API parses it. Absent or empty means the plain list. */
  q?: string;
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
              q: f.q || undefined,
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

/**
 * The fields this person may search, from the API's own echo (`query.fields` hides content fields without
 * `payload.read`). One light request (`q=` is the plain list plus the catalogue) so the help never guesses a role.
 */
export function useSearchCatalogue(project: string | null) {
  return useQuery({
    queryKey: ["search-catalogue", project],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/runs", {
          signal,
          params: { query: { limit: 1, q: "", project_id: project || undefined } },
        }),
      ).query ?? null,
    staleTime: 5 * 60_000,
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
 *
 * `enabled` goes false once the run says its trace is not stored (`trace_state` is not `available`): while retention
 * removes it the endpoint returns partial data (KI-198), so the page stops requesting and shows none of it.
 */
export function useRunEvents(runId: string, active: boolean, enabled = true) {
  const client = useQueryClient();
  const resets = useRef(0);
  const q = useInfiniteQuery({
    enabled,
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

/**
 * A run's derived findings and health. The analysis lags the events (a job), so the query polls while the run is
 * active or the analysis is in flight, and is never refetched once `current` on a finished run (findings are a
 * function of immutable events, INV-1/2).
 */
export function useRunFindings(runId: string, poll: boolean, enabled = true) {
  return useQuery({
    enabled,
    queryKey: ["run-findings", runId],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/runs/{run_id}/findings", {
          signal,
          params: { path: { run_id: runId } },
        }),
      ),
    refetchInterval: (q) => (poll || q.state.data?.state.status === "processing" ? POLL_MS : false),
    staleTime: (q) => (!poll && q.state.data?.state.status === "current" ? Infinity : 0),
  });
}

export type SecurityQuery = {
  severity: readonly string[];
  detector: readonly string[];
  projectId: string | null;
  since?: string;
  until?: string;
};
export const SECURITY_PAGE_SIZE = 10;

/** The workspace's security findings, newest run first, keyset-paged. Counts for the facets come with every page. */
export function useSecurityFindings(f: SecurityQuery, enabled = true) {
  return useInfiniteQuery({
    enabled,
    queryKey: ["security-findings", f],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }) =>
      unwrap(
        await api.GET("/v1/security/findings", {
          signal,
          params: {
            query: {
              limit: SECURITY_PAGE_SIZE,
              cursor: pageParam,
              severity: f.severity.length ? [...f.severity] : undefined,
              detector: f.detector.length ? [...f.detector] : undefined,
              project_id: f.projectId || undefined,
              since: f.since,
              until: f.until,
            },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: 15_000,
    placeholderData: keepPreviousData,
  });
}

/** The detector catalogue; with a project it says which are muted or inactive there. Fixed text, cached for a while. */
export function useSecurityDetectors(projectId: string | null | undefined, enabled = true) {
  return useQuery({
    enabled,
    queryKey: ["security-detectors", projectId ?? null],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/security/detectors", {
          signal,
          params: { query: { project_id: projectId || undefined } },
        }),
      ).items,
    staleTime: 5 * 60_000,
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

/** First chunk of an artifact's text, then one more per `fetchNextPage` (lazy: nothing loads until `enabled`). */
export const ARTIFACT_CHUNK_BYTES = 64 * 1024;
export function useArtifactText(artifactId: string | null, enabled: boolean) {
  return useInfiniteQuery({
    enabled: enabled && artifactId != null,
    queryKey: ["artifact", artifactId],
    initialPageParam: 0,
    queryFn: async ({ pageParam, signal }) =>
      unwrap(
        await api.GET("/v1/artifacts/{artifact_id}/content", {
          signal,
          params: {
            path: { artifact_id: artifactId! },
            query: { offset: pageParam, limit: ARTIFACT_CHUNK_BYTES },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_offset ?? undefined,
    staleTime: Infinity, // artifacts are immutable (INV-1)
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

/** "Where the money was wasted" (Phase 22 D12): failed-run and retry spend, by agent and model. */
export function useWasteReport(project: string | null | undefined, days: WindowDays) {
  return useQuery({
    queryKey: ["analytics", "waste", project ?? null, days],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/analytics/waste", {
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

// ------------------------------------------------------------------------- Phase 11: evaluations, compare, experiments

/** The workspace's evaluators (archived ones are not listed). Config of path-like or rubric fields is withheld by the API. */
export function useEvaluators(enabled = true) {
  return useQuery({
    enabled,
    queryKey: ["evaluators"],
    queryFn: async ({ signal }) => unwrap(await api.GET("/v1/evaluators", { signal })).items,
    staleTime: 30_000,
  });
}

/**
 * A run's evaluations, newest first. They are derived records computed by a job, so the query polls while any
 * returned row is `pending` or `running` and stops as soon as none is (a terminal row never changes, INV-1).
 */
export function useRunEvaluations(runId: string, enabled = true) {
  return useQuery({
    enabled,
    queryKey: ["run-evaluations", runId],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/runs/{run_id}/evaluations", {
          signal,
          params: { path: { run_id: runId } },
        }),
      ),
    refetchInterval: (q) =>
      q.state.data?.items.some((e) => LIVE_EVALUATION_STATES.includes(e.state)) ? POLL_MS : false,
  });
}

/** `left` is the path's run, `right` the `with_run_id` run. Computed on read; held fresh for half a minute. */
export function useComparison(leftRunId: string | null, rightRunId: string | null) {
  return useQuery({
    enabled: leftRunId != null && rightRunId != null,
    queryKey: ["compare", leftRunId, rightRunId],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/runs/{run_id}/compare", {
          signal,
          params: { path: { run_id: leftRunId! }, query: { with_run_id: rightRunId! } },
        }),
      ),
    staleTime: 30_000,
  });
}

export function useExperiments(enabled = true) {
  return useQuery({
    enabled,
    queryKey: ["experiments"],
    queryFn: async ({ signal }) => unwrap(await api.GET("/v1/experiments", { signal })).items,
  });
}

export function useExperiment(experimentId: string) {
  return useQuery({
    queryKey: ["experiment", experimentId],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/experiments/{experiment_id}", {
          signal,
          params: { path: { experiment_id: experimentId } },
        }),
      ),
  });
}

/** A write that refreshes the given queries afterwards. Errors reach the caller as `ApiRequestError`. */
function useWrite<A, R>(run: (arg: A) => Promise<R>, invalidate: (arg: A) => QueryKey[]) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: async (_data, arg) => {
      await Promise.all(invalidate(arg).map((k) => client.invalidateQueries({ queryKey: k })));
    },
  });
}

/** Failure of a body-less write (204) as a typed error. */
function checkEmpty(r: { error?: unknown; response: Response }) {
  if (!r.response.ok) unwrap({ error: r.error, response: r.response });
}

export const useTriggerEvaluation = (runId: string) =>
  useWrite(
    async (evaluatorId: string) =>
      unwrap(
        await api.POST("/v1/runs/{run_id}/evaluations", {
          params: { path: { run_id: runId } },
          body: { evaluator_id: evaluatorId },
        }),
      ),
    () => [["run-evaluations", runId]],
  );

export const useRecordVerdict = (runId: string) =>
  useWrite(
    async (body: VerdictIn) =>
      unwrap(
        await api.POST("/v1/runs/{run_id}/verdicts", {
          params: { path: { run_id: runId } },
          body,
        }),
      ),
    () => [["run-evaluations", runId], ["compare"]],
  );

export const useCreateEvaluator = () =>
  useWrite(
    async (body: EvaluatorIn) => unwrap(await api.POST("/v1/evaluators", { body })),
    () => [["evaluators"]],
  );

export const useArchiveEvaluator = () =>
  useWrite(
    async (evaluatorId: string) =>
      checkEmpty(
        await api.DELETE("/v1/evaluators/{evaluator_id}", {
          params: { path: { evaluator_id: evaluatorId } },
        }),
      ),
    () => [["evaluators"]],
  );

export const useCreateExperiment = () =>
  useWrite(
    async (body: ExperimentIn) => unwrap(await api.POST("/v1/experiments", { body })),
    () => [["experiments"]],
  );

export const useArchiveExperiment = () =>
  useWrite(
    async (experimentId: string) =>
      checkEmpty(
        await api.DELETE("/v1/experiments/{experiment_id}", {
          params: { path: { experiment_id: experimentId } },
        }),
      ),
    () => [["experiments"]],
  );

export const useAddVariant = (experimentId: string) =>
  useWrite(
    async (body: VariantIn) =>
      unwrap(
        await api.POST("/v1/experiments/{experiment_id}/variants", {
          params: { path: { experiment_id: experimentId } },
          body,
        }),
      ),
    () => [["experiment", experimentId], ["experiments"]],
  );

export const useAttachRun = (experimentId: string) =>
  useWrite(
    async (body: { run_id: string; variant_id: string }) =>
      unwrap(
        await api.POST("/v1/experiments/{experiment_id}/runs", {
          params: { path: { experiment_id: experimentId } },
          body,
        }),
      ),
    () => [["experiment", experimentId], ["experiments"]],
  );

export const useDetachRun = (experimentId: string) =>
  useWrite(
    async (runId: string) =>
      checkEmpty(
        await api.DELETE("/v1/experiments/{experiment_id}/runs/{run_id}", {
          params: { path: { experiment_id: experimentId, run_id: runId } },
        }),
      ),
    () => [["experiment", experimentId], ["experiments"]],
  );

/** Starts every evaluator of the experiment on its attached runs (500 pairs per call; `remaining` says whether to call again). */
export const useEvaluateExperiment = (experimentId: string) =>
  useWrite(
    async () =>
      unwrap(
        await api.POST("/v1/experiments/{experiment_id}/evaluate", {
          params: { path: { experiment_id: experimentId } },
          // The proxy requires a JSON body on every write (CSRF and content-type rule); the route takes none.
          body: {} as never,
        }),
      ),
    () => [["experiment", experimentId], ["run-evaluations"]],
  );
