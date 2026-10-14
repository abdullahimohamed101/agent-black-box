"use client";

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from "@tanstack/react-query";
import { api, unwrap } from "./api/client";
import type { ApprovalDetail, ApprovalSummary, ApprovalTab } from "./policy";

/** Hooks for the Approvals and Policies screens (Phase 14). The API is the authority; nothing here is optimistic. */

const APPROVALS_POLL_MS = 10_000;
const COUNT_POLL_MS = 30_000;
export const APPROVAL_PAGE = 25;

/**
 * Approvals, newest first, keyset-paged. The pending list re-reads every ~10 s. React Query does not poll a hidden tab
 * (`refetchIntervalInBackground` is off), stops with the component, and a refetch refreshes only the pages already loaded.
 */
export function useApprovals(tab: ApprovalTab, projectId: string | null, enabled: boolean) {
  return useInfiniteQuery({
    enabled,
    queryKey: ["approvals", "list", tab, projectId],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }) =>
      unwrap(
        await api.GET("/v1/approvals", {
          signal,
          params: {
            query: {
              state: tab,
              limit: APPROVAL_PAGE,
              cursor: pageParam,
              project_id: projectId || undefined,
            },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: 2000,
    refetchInterval: tab === "pending" ? APPROVALS_POLL_MS : false,
    refetchIntervalInBackground: false,
  });
}

/** The rail's count of requests still waiting. Visible tab only, every ~30 s. */
export function usePendingCount(enabled: boolean) {
  return useQuery({
    enabled,
    queryKey: ["approvals", "count"],
    queryFn: async ({ signal }) =>
      unwrap(await api.GET("/v1/approvals/pending-count", { signal })).count,
    staleTime: 5000,
    refetchInterval: COUNT_POLL_MS,
    refetchIntervalInBackground: false,
  });
}

export function useApproval(id: string | null, enabled = true) {
  return useQuery({
    enabled: enabled && id != null,
    queryKey: ["approvals", "detail", id],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/approvals/{approval_id}", {
          signal,
          params: { path: { approval_id: id! } },
        }),
      ),
    staleTime: 2000,
  });
}

type Pages = InfiniteData<{ items: ApprovalSummary[]; next_cursor: string | null }>;

/**
 * Approve or deny. What the screen shows afterwards is what the API answered: the response replaces the detail and the
 * matching row, then the lists and the count are read again (a decided request leaves the Pending list).
 */
export type DecideOutcome =
  { kind: "ok"; state: ApprovalDetail["state"] } | { kind: "error"; error: unknown };

export function useDecideApproval(id: string, onOutcome?: (o: DecideOutcome) => void) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: { decision: "approve" | "deny"; comment?: string }) =>
      unwrap(
        await api.POST("/v1/approvals/{approval_id}/decision", {
          params: { path: { approval_id: id } },
          body: { decision: body.decision, comment: body.comment || undefined },
        }),
      ),
    onSuccess: async (detail: ApprovalDetail) => {
      // First, and from here rather than from `mutate`'s callbacks: the cache write below replaces the decide form, and a
      // callback given to `mutate` is dropped with the component that called it.
      onOutcome?.({ kind: "ok", state: detail.state });
      client.setQueryData(["approvals", "detail", id], detail);
      client.setQueriesData<Pages>({ queryKey: ["approvals", "list"] }, (old) =>
        old
          ? {
              ...old,
              pages: old.pages.map((p) => ({
                ...p,
                items: p.items.map((r) => (r.id === id ? { ...r, ...summaryOf(detail) } : r)),
              })),
            }
          : old,
      );
      await client.invalidateQueries({ queryKey: ["approvals", "count"] });
      await client.invalidateQueries({ queryKey: ["audit"] });
    },
    // A lost race (409) or a refusal (403) is an answer, not a failure of the page: re-read what is true now.
    onError: async (error) => {
      onOutcome?.({ kind: "error", error });
      await client.invalidateQueries({ queryKey: ["approvals"] });
    },
  });
}

function summaryOf(d: ApprovalDetail): ApprovalSummary {
  return {
    agent_id: d.agent_id,
    approver_role: d.approver_role,
    capability: d.capability,
    consumed: d.consumed,
    decided_at: d.decided_at,
    decided_by: d.decided_by,
    expires_at: d.expires_at,
    id: d.id,
    policy_id: d.policy_id,
    policy_version: d.policy_version,
    project_id: d.project_id,
    requested_at: d.requested_at,
    resource: d.resource,
    risk_class: d.risk_class,
    run_id: d.run_id,
    seconds_left: d.seconds_left,
    state: d.state,
  };
}

// ---- policies ---------------------------------------------------------------------------------------------------

export function usePolicyCurrent(projectId: string | null, enabled = true) {
  return useQuery({
    enabled,
    queryKey: ["policy", "current", projectId],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/policies/current", {
          signal,
          params: { query: { project_id: projectId || undefined } },
        }),
      ),
    // The editor holds a draft of this document, so it must not change under the editor: it is read when the page opens,
    // after a publish (from the response) and when the person asks to reload. A newer version by someone else surfaces
    // as POLICY_VERSION_CONFLICT on publish, never as a silent replacement of the draft.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: "always",
  });
}

export function usePolicyVersions(projectId: string | null, enabled = true) {
  return useInfiniteQuery({
    enabled,
    queryKey: ["policy", "versions", projectId],
    initialPageParam: undefined as number | undefined,
    queryFn: async ({ pageParam, signal }) =>
      unwrap(
        await api.GET("/v1/policies/versions", {
          signal,
          params: {
            query: { project_id: projectId || undefined, limit: 20, before_version: pageParam },
          },
        }),
      ),
    getNextPageParam: (last) => last.next_before_version ?? undefined,
  });
}

export function usePolicyVersion(projectId: string | null, version: number | null) {
  return useQuery({
    enabled: version != null,
    queryKey: ["policy", "version", projectId, version],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/policies/versions/{version}", {
          signal,
          params: {
            path: { version: version! },
            query: { project_id: projectId || undefined },
          },
        }),
      ),
    staleTime: Infinity, // a published version is immutable
  });
}

export function usePolicyDiff(projectId: string | null, from: number | null, to: number | null) {
  return useQuery({
    enabled: from != null && to != null && from !== to,
    queryKey: ["policy", "diff", projectId, from, to],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/policies/diff", {
          signal,
          params: {
            query: { from_version: from!, to_version: to!, project_id: projectId || undefined },
          },
        }),
      ),
    staleTime: Infinity,
  });
}

export function validatePolicy(document: string, signal?: AbortSignal) {
  return api.POST("/v1/policies/validate", { body: { document }, signal }).then(unwrap);
}

export function usePublishPolicy(
  projectId: string | null,
  onPublished?: (version: number) => void,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: { document: string; baseVersion: number | null; comment: string }) =>
      unwrap(
        await api.POST("/v1/policies/publish", {
          body: {
            document: body.document,
            project_id: projectId,
            base_version: body.baseVersion,
            comment: body.comment.trim() || undefined,
          },
        }),
      ),
    onSuccess: async (data) => {
      // Before the cache write: that write remounts the editor, and a callback passed to `mutate` would be dropped with it.
      onPublished?.(data.version);
      client.setQueryData(["policy", "current", projectId], {
        policy: data,
        project_id: projectId,
        source: projectId ? "project" : "workspace",
      });
      await Promise.all([
        client.invalidateQueries({ queryKey: ["policy", "versions"] }),
        client.invalidateQueries({ queryKey: ["audit"] }),
      ]);
    },
  });
}

export function useDryRun() {
  return useMutation({
    mutationFn: async (body: { document: string; projectId: string }) =>
      unwrap(
        await api.POST("/v1/policies/dry-run", {
          body: { document: body.document, project_id: body.projectId },
        }),
      ),
  });
}
