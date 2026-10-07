import createClient from "openapi-fetch";
import type { paths } from "./schema";

/** Same-origin read proxy (ADR-021): the browser never holds an API key. */
export const PROXY_BASE = "/api/abb";

export const api = createClient<paths>({ baseUrl: PROXY_BASE });

export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly requestId: string | null,
    readonly retryable: boolean,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

type Envelope = {
  error?: { code?: string; message?: string; request_id?: string; retryable?: boolean };
};

/** Turns an openapi-fetch result into data or a typed error; never leaks a raw body into the UI. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.data !== undefined && result.response.ok) return result.data;
  const env = (typeof result.error === "object" && result.error ? result.error : {}) as Envelope;
  const status = result.response.status;
  throw new ApiRequestError(
    env.error?.message ?? `Request failed (${status})`,
    status,
    env.error?.code ?? "UNKNOWN",
    env.error?.request_id ?? result.response.headers.get("x-request-id"),
    env.error?.retryable ?? status >= 500,
  );
}
