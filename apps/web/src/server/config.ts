/**
 * Web server settings and the few rules every handler shares (ADR-060, D2/D17). Read from the environment on each
 * call so tests can stub it; nothing here is secret except `ABB_WEB_API_KEY`, which never leaves the server.
 */

export const DEFAULT_API_URL = "http://localhost:8000";
export const UPSTREAM_TIMEOUT_MS = 10_000;
export const MAX_BODY_BYTES = 64 * 1024;

/** The web app's own origin (scheme, host, port), the only `Origin` a state-changing request may carry. */
export function webOrigin(): string | null {
  const raw = process.env.ABB_WEB_ORIGIN?.trim();
  if (!raw) return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    return url.origin;
  } catch {
    return null;
  }
}

export const apiBase = (): string => process.env.ABB_API_INTERNAL_URL ?? DEFAULT_API_URL;

export const fixturesMode = (): boolean => process.env.ABB_WEB_DATA_SOURCE === "fixtures";

/** Fixtures must never stand in for real data in production unless explicitly allowed (e2e runs `next start`). */
export const fixturesBlocked = (): boolean =>
  fixturesMode() &&
  process.env.NODE_ENV === "production" &&
  process.env.ABB_WEB_ALLOW_FIXTURES !== "1";

/** The retired shared read key (D14): honoured after the session cookie until step 15 removes it. */
export const legacyKey = (): string | null => process.env.ABB_WEB_API_KEY || null;

/** Must match the API's `cookies.session_cookie_name` (one rule: https origin => `__Host-`). */
export function sessionCookieName(): string {
  return webOrigin()?.startsWith("https://") ? "__Host-abb_session" : "abb_session";
}
export const LOGIN_COOKIE = "abb_login";

const COOKIE_VALUE = /^[A-Za-z0-9_-]{16,256}$/;

/** One cookie's value from a `Cookie` header, or null when absent or not shaped like a token we issue. */
export function cookieValue(header: string | null | undefined, name: string): string | null {
  if (!header) return null;
  for (const part of header.split(";")) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    if (part.slice(0, eq).trim() !== name) continue;
    const value = part.slice(eq + 1).trim();
    return COOKIE_VALUE.test(value) ? value : null;
  }
  return null;
}

/**
 * `return_to` is a path inside this app. This is the same allowlist the API enforces (D3), applied first so the
 * login never fails over a link we could have simplified, and so nothing outside the app is ever put in a redirect.
 */
const RETURN_TO =
  /^\/(w|invite)(\/[A-Za-z0-9._~-]{1,64}){0,6}\/?(\?[A-Za-z0-9=&._~-]{0,256})?$|^\/$/;
export function safeReturnTo(value: string | null | undefined): string | null {
  if (!value || value.length > 400) return null;
  return RETURN_TO.test(value) ? value : null;
}

export const jsonSafeHeaders = {
  "cache-control": "no-store",
  "x-content-type-options": "nosniff",
} as const;

export const envelope = (status: number, code: string, message: string, retryable = false) =>
  Response.json(
    { error: { code, message, category: "WEB", retryable, request_id: null, details: {} } },
    { status, headers: jsonSafeHeaders },
  );

/** Which credential the web server uses for the visitor: their session, the legacy key, or none. */
export type Credential =
  { kind: "session"; cookie: string } | { kind: "key"; key: string } | { kind: "none" };

export function credentialFor(cookieHeader: string | null | undefined): Credential {
  const cookie = cookieValue(cookieHeader, sessionCookieName());
  if (cookie) return { kind: "session", cookie };
  const key = legacyKey();
  if (key) return { kind: "key", key };
  return { kind: "none" };
}

/** Session mode: nothing but sessions can sign anyone in, so unauthenticated visitors go to /login. */
export const sessionOnly = (): boolean => !fixturesMode() && legacyKey() === null;
