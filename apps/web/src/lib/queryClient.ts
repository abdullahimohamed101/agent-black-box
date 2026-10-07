import { QueryClient } from "@tanstack/react-query";
import { ApiRequestError } from "./api/client";

// 4xx are deterministic answers (not found, bad cursor); only transient failures are retried, twice (ADR-020).
export const shouldRetry = (count: number, err: unknown): boolean =>
  !(err instanceof ApiRequestError && err.status < 500 && err.status !== 429) && count < 2;

export const makeQueryClient = () =>
  new QueryClient({
    defaultOptions: { queries: { retry: shouldRetry, refetchOnWindowFocus: false } },
  });
