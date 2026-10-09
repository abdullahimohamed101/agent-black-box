import { fixtureReply } from "@/fixtures/api";
import {
  MAX_BODY_BYTES,
  UPSTREAM_TIMEOUT_MS,
  apiBase,
  envelope,
  fixturesBlocked,
  fixturesMode,
  jsonSafeHeaders as SAFE,
  sessionCookieFor,
  sessionCookieName,
  webOrigin,
} from "./config";

/**
 * The same-origin proxy between the browser and the API (ADR-060, which supersedes ADR-021). The browser holds a session
 * cookie only; this server forwards it, and nothing else, to the API. What is forwarded is an allowlist (D17):
 * the session cookie, `X-ABB-Workspace`, `Origin`, `Last-Event-ID`, `Accept` and a JSON body. `Authorization`,
 * other cookies, `X-Forwarded-*`, `Forwarded` and `X-Request-ID` from the browser never reach the API, and
 * `Set-Cookie` never comes back (sign-in has its own handlers). The web server has no credential of its own.
 */

const ID = "[A-Za-z0-9_-]{1,64}";
type Route = { method: string; re: RegExp };
const route = (method: string, pattern: string): Route => ({
  method,
  re: new RegExp(`^v1/${pattern}$`),
});
const ROUTES: readonly Route[] = [
  route(
    "GET",
    `(runs(/${ID}(/(spans|stream|events(/${ID})?))?)?|artifacts/${ID}(/content)?|analytics/(summary|cost|reliability|performance))`,
  ),
  route("GET", "(me|projects|members|invitations|api-keys|pricing|audit)"),
  route(
    "POST",
    "(projects|invitations|invitations/accept|api-keys|pricing/overrides|cost/rebuild)",
  ),
  route("PATCH", `members/${ID}`),
  route("DELETE", `(members|invitations|api-keys)/${ID}`),
];
const STREAM = new RegExp(`^v1/runs/${ID}/stream$`);
const MAX_QUERY_CHARS = 2048;
const LAST_EVENT_ID = /^[A-Za-z0-9_-]{1,64}$/;
const WORKSPACE_ID = /^[A-Za-z0-9_-]{1,64}$/;

export type ReadOptions = {
  method?: string;
  /** The browser's request headers; only the allowlisted ones are used. */
  headers?: Headers;
  /** The browser's request body (JSON writes); read with a hard cap. */
  body?: ReadableStream<Uint8Array> | null;
  /** The browser's `Last-Event-ID` header, forwarded so a reconnect resumes (streams only). */
  lastEventId?: string | null;
  /** Aborted when the browser goes away: the upstream stream is closed with it. */
  signal?: AbortSignal;
};

type Prepared =
  | { ok: false; response: Response }
  | {
      ok: true;
      url: string;
      method: string;
      headers: Record<string, string>;
      body: string | undefined;
    };

async function readCapped(
  stream: ReadableStream<Uint8Array> | null | undefined,
  declared: string | null,
): Promise<string | null> {
  if (declared !== null && Number(declared) > MAX_BODY_BYTES) return null;
  if (!stream) return "";
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_BODY_BYTES) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  return new TextDecoder().decode(Buffer.concat(chunks));
}

async function prepare(
  segments: string[],
  search: URLSearchParams,
  options: ReadOptions,
): Promise<Prepared> {
  const fail = (response: Response): Prepared => ({ ok: false, response });
  const method = (options.method ?? "GET").toUpperCase();
  const path = segments.join("/");
  const known = ROUTES.filter((r) => r.re.test(path));
  if (known.length === 0) {
    // Unknown paths keep the contract from before writes existed: GET is "not found", anything else "not allowed".
    if (method === "GET") return fail(envelope(404, "NOT_FOUND", "Not found."));
    const res = envelope(405, "METHOD_NOT_ALLOWED", "Method not allowed.");
    res.headers.set("allow", "GET");
    return fail(res);
  }
  if (!known.some((r) => r.method === method)) {
    const res = envelope(405, "METHOD_NOT_ALLOWED", "Method not allowed.");
    res.headers.set("allow", known.map((r) => r.method).join(", "));
    return fail(res);
  }
  const query = new URLSearchParams(search);
  const workspaceParam = query.get("workspace"); // EventSource cannot set headers: streams carry it here
  query.delete("workspace");
  if (query.toString().length > MAX_QUERY_CHARS)
    return fail(envelope(414, "QUERY_TOO_LONG", "Query string too long."));

  const incoming = options.headers ?? new Headers();
  const session = sessionCookieFor(incoming.get("cookie"));
  if (method !== "GET") {
    const origin = webOrigin();
    if (!origin) return fail(envelope(503, "WEB_NOT_CONFIGURED", "ABB_WEB_ORIGIN is not set."));
    // Exact match, scheme and port included. `null` and a missing header are refused (D17).
    if (incoming.get("origin") !== origin)
      return fail(envelope(403, "CSRF_REJECTED", "Cross-origin request refused."));
  }
  if (!session) return fail(envelope(401, "SESSION_INVALID", "Sign in to continue."));

  const headers: Record<string, string> = {
    accept: STREAM.test(path) ? "text/event-stream" : "application/json",
    cookie: `${sessionCookieName()}=${session}`,
  };
  const workspace = incoming.get("x-abb-workspace") ?? workspaceParam;
  if (workspace && WORKSPACE_ID.test(workspace)) headers["x-abb-workspace"] = workspace;
  const lastEventId = options.lastEventId ?? incoming.get("last-event-id");
  if (lastEventId && LAST_EVENT_ID.test(lastEventId)) headers["last-event-id"] = lastEventId;

  let body: string | undefined;
  if (method === "POST" || method === "PATCH") {
    if (!(incoming.get("content-type") ?? "").toLowerCase().startsWith("application/json"))
      return fail(envelope(415, "UNSUPPORTED_MEDIA_TYPE", "A JSON body is required."));
    const text = await readCapped(options.body, incoming.get("content-length"));
    if (text === null) return fail(envelope(413, "BODY_TOO_LARGE", "Request body too large."));
    body = text;
    headers["content-type"] = "application/json";
    headers.origin = webOrigin()!; // the API applies the same exact-origin rule to cookie writes
  } else if (method === "DELETE") {
    headers.origin = webOrigin()!;
  }
  const qs = query.toString();
  return {
    ok: true,
    url: `${apiBase()}/${path}${qs ? `?${qs}` : ""}`,
    method,
    headers,
    body,
  };
}

const redirected = () =>
  envelope(502, "WEB_UPSTREAM_REDIRECT", "The API answered with a redirect.");

function relayHeaders(upstream: Response): Headers {
  const headers = new Headers({ "content-type": "application/json", ...SAFE });
  for (const name of ["x-request-id", "retry-after"]) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  return headers;
}

export async function readThrough(
  segments: string[],
  search: URLSearchParams,
  options: ReadOptions = {},
): Promise<Response> {
  const path = segments.join("/");
  if (fixturesBlocked() && ROUTES.some((r) => r.re.test(path))) {
    return envelope(503, "FIXTURES_DISABLED", "Fixture data is disabled in production.");
  }
  if (fixturesMode() && !fixturesBlocked() && ROUTES.some((r) => r.re.test(path))) {
    if (STREAM.test(path))
      return envelope(404, "STREAM_NOT_AVAILABLE", "Fixture data has no live stream.");
    if ((options.method ?? "GET").toUpperCase() !== "GET")
      return envelope(405, "FIXTURE_READ_ONLY", "Fixture data is read-only.");
    if (search.toString().length > MAX_QUERY_CHARS)
      return envelope(414, "QUERY_TOO_LONG", "Query string too long.");
    const r = fixtureReply(segments, search);
    return Response.json(r.body, {
      status: r.status,
      headers: { ...SAFE, "x-request-id": "req_fixture" },
    });
  }
  const prepared = await prepare(segments, search, options);
  if (!prepared.ok) return prepared.response;
  if (STREAM.test(path)) return streamThrough(prepared, options.signal);
  try {
    const upstream = await fetch(prepared.url, {
      method: prepared.method,
      headers: prepared.headers,
      body: prepared.body,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
    if (upstream.status >= 300 && upstream.status < 400) {
      await upstream.body?.cancel();
      return redirected();
    }
    const headers = relayHeaders(upstream);
    if (upstream.status === 204) headers.delete("content-type");
    return new Response(upstream.status === 204 ? null : upstream.body, {
      status: upstream.status,
      headers,
    });
  } catch {
    return envelope(503, "API_UNREACHABLE", "The API could not be reached.", true);
  }
}

/**
 * Live run events (SSE). Unlike reads, the body is relayed as it arrives and has no overall deadline:
 * only reaching the API is time-limited.
 */
async function streamThrough(
  prepared: Extract<Prepared, { ok: true }>,
  signal: AbortSignal | undefined,
): Promise<Response> {
  const connect = new AbortController();
  const timer = setTimeout(() => connect.abort(), UPSTREAM_TIMEOUT_MS);
  const anySignal = signal ? AbortSignal.any([connect.signal, signal]) : connect.signal;
  try {
    const upstream = await fetch(prepared.url, {
      headers: prepared.headers,
      redirect: "manual",
      cache: "no-store",
      signal: anySignal,
    });
    clearTimeout(timer); // connected: from here on the stream may run for as long as the API allows
    if (upstream.status >= 300 && upstream.status < 400) {
      await upstream.body?.cancel();
      return redirected();
    }
    const type = upstream.headers.get("content-type") ?? "";
    if (!upstream.ok || !type.startsWith("text/event-stream")) {
      // An API error (401, 404, 429 STREAM_LIMIT, 503...) is a normal JSON envelope: relay it unchanged.
      return new Response(upstream.body, {
        status: upstream.status,
        headers: relayHeaders(upstream),
      });
    }
    return new Response(upstream.body, {
      status: 200,
      headers: {
        "content-type": "text/event-stream",
        "cache-control": "no-store, no-transform", // no-transform: nothing may re-encode or buffer the stream
        "x-accel-buffering": "no",
        "x-content-type-options": "nosniff",
        ...(upstream.headers.get("x-request-id")
          ? { "x-request-id": upstream.headers.get("x-request-id")! }
          : {}),
      },
    });
  } catch {
    clearTimeout(timer);
    return envelope(503, "API_UNREACHABLE", "The API could not be reached.", true);
  }
}
