import { QueryClient } from "@tanstack/react-query";
import { ApiRequestError } from "./api/client";

// 4xx are deterministic answers (not found, bad cursor); only transient failures are retried, twice (ADR-020).
// A busy or timed-out search is shown at once with a retry button: retrying it automatically would only add load.
const NO_AUTO_RETRY = new Set(["SEARCH_BUSY", "SEARCH_TIMEOUT"]);
export const shouldRetry = (count: number, err: unknown): boolean =>
  !(err instanceof ApiRequestError && err.status < 500 && err.status !== 429) &&
  !(err instanceof ApiRequestError && NO_AUTO_RETRY.has(err.code)) &&
  count < 2;

export const makeQueryClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: shouldRetry, refetchOnWindowFocus: false } },
  });
