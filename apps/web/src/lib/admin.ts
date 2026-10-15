"use client";

import { useMutation, useQuery, useQueryClient, useInfiniteQuery } from "@tanstack/react-query";
import { api, unwrap } from "./api/client";
import type { components } from "./api/schema";

/** Data hooks for the settings pages. Rights are decided by the API; these only carry requests and results. */

export const useMembers = (enabled = true) =>
  useQuery({
    enabled,
    queryKey: ["members"],
    queryFn: async ({ signal }) => unwrap(await api.GET("/v1/members", { signal })).items,
  });

export const useInvitations = (enabled: boolean) =>
  useQuery({
    enabled,
    queryKey: ["invitations"],
    queryFn: async ({ signal }) => unwrap(await api.GET("/v1/invitations", { signal })).items,
  });

/** The signed-in person's own sessions (any role; the API answers 401 to a key). */
export const useSessions = () =>
  useQuery({
    queryKey: ["sessions"],
    queryFn: async ({ signal }) => unwrap(await api.GET("/v1/auth/sessions", { signal })).items,
  });

export const useApiKeys = (enabled: boolean) =>
  useQuery({
    enabled,
    queryKey: ["api-keys"],
    queryFn: async ({ signal }) => unwrap(await api.GET("/v1/api-keys", { signal })).items,
  });

export const usePrices = (enabled: boolean) =>
  useQuery({
    enabled,
    queryKey: ["pricing"],
    queryFn: async ({ signal }) => unwrap(await api.GET("/v1/pricing", { signal })).prices,
  });

export const useAudit = (enabled: boolean) =>
  useInfiniteQuery({
    enabled,
    queryKey: ["audit"],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }) =>
      unwrap(
        await api.GET("/v1/audit", { signal, params: { query: { cursor: pageParam, limit: 50 } } }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });

/** One project's capture mode, detector mutes and destination allowlist (project.write only; the API says 403 to others). */
export const useSecuritySettings = (projectId: string | null, enabled = true) =>
  useQuery({
    enabled: enabled && projectId != null,
    queryKey: ["security-settings", projectId],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/projects/{project_id}/security-settings", {
          signal,
          params: { path: { project_id: projectId! } },
        }),
      ),
  });

/** One project's quota limits and use (any role that reads projects; the API says 404 for a project it may not show). */
export const useProjectQuotas = (projectId: string | null) =>
  useQuery({
    enabled: projectId != null,
    queryKey: ["project-quotas", projectId],
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/projects/{project_id}/quotas", {
          signal,
          params: { path: { project_id: projectId! } },
        }),
      ),
  });

/** A write that refreshes the listed queries afterwards. Errors reach the caller as `ApiRequestError`. */
function useWrite<A, R>(run: (arg: A) => Promise<R>, invalidate: string[]) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: async () => {
      await Promise.all(invalidate.map((k) => client.invalidateQueries({ queryKey: [k] })));
    },
  });
}

type Role = components["schemas"]["ChangeRole"]["role"];

export const useInvite = () =>
  useWrite(
    async (body: components["schemas"]["InviteIn"]) =>
      unwrap(await api.POST("/v1/invitations", { body })),
    ["invitations"],
  );
export const useRevokeInvitation = () =>
  useWrite(
    async (id: string) => {
      const r = await api.DELETE("/v1/invitations/{invitation_id}", {
        params: { path: { invitation_id: id } },
      });
      if (!r.response.ok) unwrap({ error: r.error, response: r.response });
    },
    ["invitations"],
  );
export const useChangeRole = () =>
  useWrite(
    async (a: { userId: string; role: Role }) =>
      unwrap(
        await api.PATCH("/v1/members/{user_id}", {
          params: { path: { user_id: a.userId } },
          body: { role: a.role },
        }),
      ),
    ["members"],
  );
export const useRemoveMember = () =>
  useWrite(
    async (userId: string) => {
      const r = await api.DELETE("/v1/members/{user_id}", {
        params: { path: { user_id: userId } },
      });
      if (!r.response.ok) unwrap({ error: r.error, response: r.response });
    },
    ["members"],
  );
export const useRevokeSession = () =>
  useWrite(
    async (sessionId: string) => {
      const r = await api.DELETE("/v1/auth/sessions/{session_id}", {
        params: { path: { session_id: sessionId } },
      });
      if (!r.response.ok) unwrap({ error: r.error, response: r.response });
    },
    ["sessions", "audit"],
  );
/** Ends every session but the current one; resolves to how many it ended. */
export const useRevokeOtherSessions = () =>
  useWrite(
    async () => unwrap(await api.POST("/v1/auth/sessions/revoke-others", {})).revoked,
    ["sessions", "audit"],
  );
export const useCreateKey = () =>
  useWrite(
    async (body: components["schemas"]["CreateKey"]) =>
      unwrap(await api.POST("/v1/api-keys", { body })),
    ["api-keys", "audit"],
  );
export const useRevokeKey = () =>
  useWrite(
    async (keyId: string) => {
      const r = await api.DELETE("/v1/api-keys/{key_id}", { params: { path: { key_id: keyId } } });
      if (!r.response.ok) unwrap({ error: r.error, response: r.response });
    },
    ["api-keys", "audit"],
  );
export const useCreateOverride = () =>
  useWrite(
    async (body: components["schemas"]["CreateOverride"]) =>
      unwrap(await api.POST("/v1/pricing/overrides", { body })),
    ["pricing", "audit"],
  );
export const useRebuildCosts = () =>
  useWrite(
    async (body: components["schemas"]["RebuildRequest"]) =>
      unwrap(await api.POST("/v1/cost/rebuild", { body })),
    ["audit"],
  );

/**
 * Change a project's quota overrides (owners and admins). Like the security settings, the cache takes the API's answer:
 * what the page shows after a save is what the API stored.
 */
export const useUpdateProjectQuotas = (projectId: string) => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: components["schemas"]["QuotaOverridesIn"]) =>
      unwrap(
        await api.PUT("/v1/projects/{project_id}/quotas", {
          params: { path: { project_id: projectId } },
          body,
        }),
      ),
    onSuccess: async (data) => {
      client.setQueryData(["project-quotas", projectId], data);
      await client.invalidateQueries({ queryKey: ["audit"] });
    },
  });
};

/**
 * Partial update (a list given replaces the stored one; fields left out stay). The settings query is refreshed from
 * the response by the caller, not guessed: what the screen shows after a save is what the API stored.
 */
export const useUpdateSecuritySettings = (projectId: string) => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: components["schemas"]["SecuritySettingsIn"]) =>
      unwrap(
        await api.PUT("/v1/projects/{project_id}/security-settings", {
          params: { path: { project_id: projectId } },
          body,
        }),
      ),
    onSuccess: async (data) => {
      client.setQueryData(["security-settings", projectId], data);
      await Promise.all([
        client.invalidateQueries({ queryKey: ["audit"] }),
        client.invalidateQueries({ queryKey: ["security-detectors"] }),
      ]);
    },
  });
};
