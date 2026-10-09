import {
  LOGIN_COOKIE,
  UPSTREAM_TIMEOUT_MS,
  apiBase,
  cookieValue,
  envelope,
  fixturesMode,
  safeReturnTo,
  sessionCookieName,
  webOrigin,
} from "./config";
import { clientKey, loginLimiter } from "./loginLimiter";

/**
 * Sign-in relays (D1, D17). The API owns identity and cookies; these handlers only carry the browser's
 * `abb_login`/session cookie to it and bring its `Location` and `Set-Cookie` back. They never follow a redirect
 * (the IdP is for the browser), forward nothing but what each call needs, and relay only cookies we issue.
 */

const ISSUED_COOKIES = () => new Set([sessionCookieName(), LOGIN_COOKIE]);

export type LoginError = "login_failed" | "rate_limited" | "unavailable" | "not_configured";

/** A redirect with no body; `cookies` are `Set-Cookie` values from the API, relayed untouched. */
function redirect(location: string, status: 302 | 303, cookies: readonly string[] = []): Response {
  const headers = new Headers({ location, "cache-control": "no-store" });
  for (const c of cookies) headers.append("set-cookie", c);
  return new Response(null, { status, headers });
}

const toLogin = (error: LoginError, cookies: readonly string[] = [], status: 302 | 303 = 302) =>
  redirect(`/login?error=${error}`, status, cookies);

/** The `Set-Cookie` lines of an API response whose cookie name is one of ours (never anything else). */
export function issuedCookies(upstream: Response): string[] {
  const allowed = ISSUED_COOKIES();
  return upstream.headers
    .getSetCookie()
    .filter((line) => allowed.has(line.split("=", 1)[0]!.trim()));
}

function errorFor(status: number): LoginError {
  if (status === 429) return "rate_limited";
  if (status === 503) return "unavailable";
  return "login_failed";
}

/** Only an absolute http(s) URL without credentials may be a sign-in destination (the identity provider). */
function externalUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if ((url.protocol !== "https:" && url.protocol !== "http:") || url.username || url.password)
      return null;
    return url.toString();
  } catch {
    return null;
  }
}

function throttled(request: Request): Response | null {
  const wait = loginLimiter().acquire(clientKey(request.headers));
  if (wait === null) return null;
  const res = toLogin("rate_limited");
  res.headers.set("retry-after", String(wait));
  return res;
}

async function callApi(path: string, init: RequestInit): Promise<Response | null> {
  try {
    return await fetch(`${apiBase()}${path}`, {
      ...init,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch {
    return null;
  }
}

/** `GET /api/auth/login?return_to=` */
export async function loginRoute(request: Request): Promise<Response> {
  if (fixturesMode()) return redirect("/", 302);
  const limited = throttled(request);
  if (limited) return limited;
  const returnTo = safeReturnTo(new URL(request.url).searchParams.get("return_to"));
  const query = returnTo ? `?return_to=${encodeURIComponent(returnTo)}` : "";
  const upstream = await callApi(`/v1/auth/login${query}`, {
    headers: { accept: "application/json" },
  });
  if (!upstream) return toLogin("unavailable");
  await upstream.body?.cancel();
  const target = externalUrl(upstream.headers.get("location"));
  if (upstream.status !== 302 || !target)
    return toLogin(upstream.status === 503 ? "not_configured" : errorFor(upstream.status));
  return redirect(target, 302, issuedCookies(upstream));
}

const BOUNDS = { code: 2048, state: 128, error: 200 } as const;

/** `GET /api/auth/callback?code&state` (or `error`): the identity provider's redirect target. */
export async function callbackRoute(request: Request): Promise<Response> {
  if (fixturesMode()) return redirect("/", 302);
  const limited = throttled(request);
  if (limited) return limited;
  const incoming = new URL(request.url).searchParams;
  const forward = new URLSearchParams();
  for (const name of ["code", "state", "error"] as const) {
    const value = incoming.get(name);
    if (value !== null && value.length <= BOUNDS[name]) forward.set(name, value);
  }
  const login = cookieValue(request.headers.get("cookie"), LOGIN_COOKIE);
  const upstream = await callApi(`/v1/auth/callback?${forward.toString()}`, {
    headers: {
      accept: "application/json",
      ...(login ? { cookie: `${LOGIN_COOKIE}=${login}` } : {}),
    },
  });
  if (!upstream) return toLogin("unavailable");
  const cookies = issuedCookies(upstream); // on failure this is just the spent login cookie being cleared
  await upstream.body?.cancel();
  if (upstream.status !== 302) return toLogin(errorFor(upstream.status), cookies);
  // The API validated this path; it is checked again because it becomes a redirect on our origin.
  const raw = upstream.headers.get("location");
  return redirect(safeReturnTo(raw) ?? "/", 302, cookies);
}

/** `POST /api/auth/logout`: same-origin only; revokes the session at the API and clears the cookie. */
export async function logoutRoute(request: Request): Promise<Response> {
  const origin = webOrigin();
  if (!origin) return envelope(503, "WEB_NOT_CONFIGURED", "ABB_WEB_ORIGIN is not set.");
  if (request.headers.get("origin") !== origin)
    return envelope(403, "CSRF_REJECTED", "Cross-origin request refused.");
  const session = cookieValue(request.headers.get("cookie"), sessionCookieName());
  if (!session) return redirect("/login", 303); // nothing to revoke: signing out twice is fine
  const upstream = await callApi("/v1/auth/logout", {
    method: "POST",
    headers: { accept: "application/json", origin, cookie: `${sessionCookieName()}=${session}` },
  });
  if (!upstream)
    return envelope(503, "API_UNREACHABLE", "Could not sign out; the API is unreachable.", true);
  const cookies = issuedCookies(upstream);
  await upstream.body?.cancel();
  if (!upstream.ok) return redirect("/login?error=logout_failed", 303, cookies);
  return redirect("/login", 303, cookies);
}
