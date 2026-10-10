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
