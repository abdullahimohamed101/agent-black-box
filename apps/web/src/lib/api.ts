/** `GET /readyz` (ADR-169): `checks` carries a fixed reason code per check, never error text. */
export type ReadyResponse = {
  status: "ready" | "not_ready";
  version: string;
  checks: Record<string, { status: "ok" | "fail" | "degraded"; reason: string | null }>;
};

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

/** Checks API readiness through the web server (`/api/health`). Never throws: the UI renders every outcome. */
export async function fetchReadiness(
  fetchImpl: typeof fetch = fetch,
  baseUrl: string = "",
): Promise<HealthResult> {
  try {
    const response = await fetchImpl(`${baseUrl}/api/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(4000),
    });
    const body: unknown = await response.json();
    if (response.ok) {
      return { state: "ok", version: (body as ReadyResponse).version };
    }
    const error = (body as { error?: ApiError }).error;
    const failing = Object.entries((body as Partial<ReadyResponse>).checks ?? {})
      .filter(([, check]) => check.status === "fail")
      .map(([name, check]) => `${name}: ${check.reason ?? "fail"}`);
    return {
      state: "error",
      message:
        error?.message ??
        (failing.length > 0
          ? `API is not ready (${failing.join(", ")})`
          : `API returned ${response.status}`),
      requestId: error?.request_id ?? response.headers.get("x-request-id"),
    };
  } catch (cause) {
    return {
      state: "unreachable",
      message: cause instanceof Error ? cause.message : "Request failed",
    };
  }
}
