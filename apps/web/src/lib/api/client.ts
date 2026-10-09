import createClient from "openapi-fetch";
import type { paths } from "./schema";

/** Same-origin read proxy (ADR-021): the browser never holds an API key. */
export const PROXY_BASE = "/api/abb";

/**
 * The workspace this tab is looking at (`ws_...`), sent as `X-ABB-Workspace` on every call (D8). It is module state
 * so that each browser tab carries its own; `WorkspaceProvider` sets it. Unset on pages outside a workspace.
 */
let currentWorkspace: string | null = null;
export const getApiWorkspace = (): string | null => currentWorkspace;
export const setApiWorkspace = (id: string | null) => {
  currentWorkspace = id;
};

/** Where an expired or revoked session sends the visitor. Replaced in tests. */
export let onSessionExpired = (): void => {
  if (typeof location === "undefined" || location.pathname === "/login") return;
  const here = `${location.pathname}${location.search}`;
  // A full navigation on purpose: it drops all client state of the expired session (outside React, no router).
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.href = `/login?return_to=${encodeURIComponent(here)}`;
};
export const setSessionExpiredHandler = (handler: () => void) => {
  onSessionExpired = handler;
};

async function send(request: Request): Promise<Response> {
  if (currentWorkspace) request.headers.set("x-abb-workspace", currentWorkspace);
  const response = await globalThis.fetch(request);
  if (response.status === 401) {
    const code = await response
      .clone()
      .json()
      .then((b: { error?: { code?: string } }) => b.error?.code)
      .catch(() => undefined);
    if (code === "SESSION_INVALID") onSessionExpired();
  }
  return response;
}

// Absolute URL and call-time `fetch` lookup: Request needs an absolute URL outside a real page, and tests stub global fetch.
const origin = typeof location !== "undefined" ? location.origin : "http://localhost";
export const api = createClient<paths>({
  baseUrl: `${origin}${PROXY_BASE}`,
  fetch: send,
});

export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly requestId: string | null,
    readonly retryable: boolean,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

type Envelope = {
  error?: {
    code?: string;
    message?: string;
    request_id?: string;
    retryable?: boolean;
    details?: Record<string, unknown>;
  };
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
    env.error?.details ?? {},
  );
}

/** `{field: message}` from a 422 (`details.errors[].loc`), so forms can show the API's validation per field. */
export function fieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiRequestError) || error.status !== 422) return {};
  const list = (error.details as { errors?: { loc?: unknown[]; msg?: string }[] }).errors ?? [];
  const out: Record<string, string> = {};
  for (const e of list) {
    const field = e.loc?.[e.loc.length - 1];
    if (typeof field === "string" && e.msg && !(field in out)) out[field] = e.msg;
  }
  return out;
}
