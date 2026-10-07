export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type ReadyResponse = { status: "ok"; version: string; database: "ok" };

export type ApiError = {
  code: string;
  message: string;
  retryable: boolean;
  request_id: string | null;
};

export type HealthResult =
  | { state: "ok"; version: string }
  | { state: "error"; message: string; requestId: string | null }
  | { state: "unreachable"; message: string };

/** Checks API readiness. Never throws: the UI renders every outcome. */
export async function fetchReadiness(
  fetchImpl: typeof fetch = fetch,
  baseUrl: string = API_URL,
): Promise<HealthResult> {
  try {
    const response = await fetchImpl(`${baseUrl}/readyz`, {
      cache: "no-store",
      signal: AbortSignal.timeout(4000),
    });
    const body: unknown = await response.json();
    if (response.ok) {
      return { state: "ok", version: (body as ReadyResponse).version };
    }
    const error = (body as { error?: ApiError }).error;
    return {
      state: "error",
      message: error?.message ?? `API returned ${response.status}`,
      requestId: error?.request_id ?? response.headers.get("x-request-id"),
    };
  } catch (cause) {
    return {
      state: "unreachable",
      message: cause instanceof Error ? cause.message : "Request failed",
    };
  }
}
