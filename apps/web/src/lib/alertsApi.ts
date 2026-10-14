"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "./api/client";
import type { components } from "./api/schema";
import type { AlertNotification, RuleCreate } from "./alerts";

/** Hooks for the Alerts page (Phase 17). The API decides who may do what; nothing here is optimistic. */

export const HISTORY_PAGE = 25;

export const useAlertRules = (enabled = true) =>
  useQuery({
    enabled,
    queryKey: ["alerts", "rules"],
    queryFn: async ({ signal }) => unwrap(await api.GET("/v1/alerts/rules", { signal })).items,
  });

/** Owners and admins only. Everyone else is a 403 that the page explains instead of asking at all. */
export const useAlertDestinations = (enabled: boolean) =>
  useQuery({
    enabled,
    queryKey: ["alerts", "destinations"],
    queryFn: async ({ signal }) => unwrap(await api.GET("/v1/alerts/destinations", { signal })),
  });

export type HistoryFilters = {
  status?: AlertNotification["status"];
  trigger?: AlertNotification["trigger"];
  rule_id?: string;
  destination_id?: string;
};

export const useAlertHistory = (filters: HistoryFilters, enabled = true) =>
  useInfiniteQuery({
    enabled,
    queryKey: ["alerts", "history", filters],
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam, signal }) =>
      unwrap(
        await api.GET("/v1/alerts/notifications", {
          signal,
          params: { query: { ...filters, limit: HISTORY_PAGE, cursor: pageParam } },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: 5000,
  });

/** What the saved rule would have fired over the last N days. Counts only; a slow answer is a 504 the caller words. */
export const useRulePreview = (ruleId: string | null, days = 7) =>
  useQuery({
    enabled: ruleId != null,
    queryKey: ["alerts", "preview", ruleId, days],
    retry: false,
    staleTime: 30_000,
    queryFn: async ({ signal }) =>
      unwrap(
        await api.GET("/v1/alerts/rules/{rule_id}/preview", {
          signal,
          params: { path: { rule_id: ruleId! }, query: { days } },
        }),
      ),
  });

function useWrite<A, R>(run: (arg: A) => Promise<R>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: run,
    // A destination's variables hold its target URL (a secret in itself, for a Slack hook): drop the
    // mutation from the cache as soon as nothing observes it instead of keeping it for minutes.
    gcTime: 0,
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
}

export const useCreateRule = () =>
  useWrite(async (body: RuleCreate) => unwrap(await api.POST("/v1/alerts/rules", { body })));
export const useUpdateRule = () =>
  useWrite(async (a: { id: string; body: components["schemas"]["RulePatch"] }) =>
    unwrap(
      await api.PATCH("/v1/alerts/rules/{rule_id}", {
        params: { path: { rule_id: a.id } },
        body: a.body,
      }),
    ),
  );
export const useDeleteRule = () =>
  useWrite(async (id: string) => {
    const r = await api.DELETE("/v1/alerts/rules/{rule_id}", { params: { path: { rule_id: id } } });
    if (!r.response.ok) unwrap({ error: r.error, response: r.response });
  });

/**
 * A signing secret exists in exactly one place: the callback that the caller passes with the call. It is removed from
 * the result before React Query sees it, so it never enters the mutation cache (which keeps results for minutes).
 */
export type SecretSink = (secret: { destinationName: string; secret: string }) => void;
type Stripped<T> = Omit<T, "signing_secret">;
function strip<T extends { name: string; signing_secret: string | null }>(
  created: T,
  sink: SecretSink | undefined,
): Stripped<T> {
  const { signing_secret: secret, ...rest } = created;
  if (secret && sink) sink({ destinationName: created.name, secret });
  return rest;
}

export const useCreateDestination = () =>
  useWrite(async (a: { body: components["schemas"]["DestinationCreate"]; onSecret?: SecretSink }) =>
    strip(unwrap(await api.POST("/v1/alerts/destinations", { body: a.body })), a.onSecret),
  );
export const useUpdateDestination = () =>
  useWrite(
    async (a: {
      id: string;
      body: components["schemas"]["DestinationPatch"];
      onSecret?: SecretSink;
    }) =>
      strip(
        unwrap(
          await api.PATCH("/v1/alerts/destinations/{destination_id}", {
            params: { path: { destination_id: a.id } },
            body: a.body,
          }),
        ),
        a.onSecret,
      ),
  );
export const useDeleteDestination = () =>
  useWrite(async (id: string) => {
    const r = await api.DELETE("/v1/alerts/destinations/{destination_id}", {
      params: { path: { destination_id: id } },
    });
    if (!r.response.ok) unwrap({ error: r.error, response: r.response });
  });
/** Sends the fixed test alert. It changes nothing stored, so no list is refreshed. */
export const useTestDestination = () =>
  useMutation({
    gcTime: 0,
    mutationFn: async (id: string) =>
      unwrap(
        await api.POST("/v1/alerts/destinations/{destination_id}/test", {
          params: { path: { destination_id: id } },
          // The route takes no body, but the proxy requires a JSON content type on every POST (D17).
          body: {} as never,
        }),
      ),
  });
