import { fixtureReply } from "@/fixtures/api";

/** Read paths the browser may reach (ADR-021). Anything else is a 404, even though the API would reject writes anyway. */
const ALLOWED =
  /^v1\/runs(\/[A-Za-z0-9_-]{1,64}(\/(spans|stream|events(\/[A-Za-z0-9_-]{1,64})?))?)?$/;
const STREAM = /^v1\/runs\/[A-Za-z0-9_-]{1,64}\/stream$/;
const TIMEOUT_MS = 10_000;
const MAX_QUERY_CHARS = 2048;
// Proxy responses carry tenant data: never cacheable, never sniffed.
const SAFE = { "cache-control": "no-store", "x-content-type-options": "nosniff" } as const;

const envelope = (status: number, code: string, message: string, retryable = false) =>
  Response.json(
    { error: { code, message, category: "WEB", retryable, request_id: null, details: {} } },
    { status, headers: SAFE },
  );

export type ReadOptions = {
  /** The browser's `Last-Event-ID` header, forwarded so a reconnect resumes (streams only). */
  lastEventId?: string | null;
  /** Aborted when the browser goes away: the upstream stream is closed with it. */
  signal?: AbortSignal;
};

const LAST_EVENT_ID = /^[A-Za-z0-9_-]{1,64}$/;

export async function readThrough(
  segments: string[],
  search: URLSearchParams,
  options: ReadOptions = {},
): Promise<Response> {
  const path = segments.join("/");
  if (!ALLOWED.test(path)) return envelope(404, "NOT_FOUND", "Not found.");
  if (STREAM.test(path)) return streamThrough(segments, search, options);
  if (search.toString().length > MAX_QUERY_CHARS)
    return envelope(414, "QUERY_TOO_LONG", "Query string too long.");
  if (process.env.ABB_WEB_DATA_SOURCE === "fixtures") {
    // Fixtures must never stand in for real data in production unless explicitly allowed (e2e runs `next start`).
    if (process.env.NODE_ENV === "production" && process.env.ABB_WEB_ALLOW_FIXTURES !== "1") {
      return envelope(503, "FIXTURES_DISABLED", "Fixture data is disabled in production.");
    }
    const r = fixtureReply(segments, search);
    return Response.json(r.body, {
      status: r.status,
      headers: { ...SAFE, "x-request-id": "req_fixture" },
    });
  }
  const key = process.env.ABB_WEB_API_KEY;
  if (!key) {
    return envelope(503, "WEB_NOT_CONFIGURED", "The web server has no ABB_WEB_API_KEY configured.");
  }
  const base = process.env.ABB_API_INTERNAL_URL ?? "http://localhost:8000";
  const qs = search.toString();
  try {
    const upstream = await fetch(`${base}/${segments.join("/")}${qs ? `?${qs}` : ""}`, {
      headers: { authorization: `Bearer ${key}`, accept: "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const headers = new Headers({ "content-type": "application/json", ...SAFE });
    const rid = upstream.headers.get("x-request-id");
    if (rid) headers.set("x-request-id", rid);
    const ra = upstream.headers.get("retry-after");
    if (ra) headers.set("retry-after", ra);
    // Upstream 401/403 mean the proxy's own key is wrong: do not pass them through as if the visitor's login failed.
    if (upstream.status === 401 || upstream.status === 403) {
      return envelope(
        502,
        "WEB_UPSTREAM_AUTH",
        "The web server's API key was rejected by the API.",
      );
    }
    return new Response(upstream.body, { status: upstream.status, headers });
  } catch {
    return envelope(503, "API_UNREACHABLE", "The API could not be reached.", true);
  }
}

/**
 * Live run events (SSE). Unlike reads, the body is relayed as it arrives and has no overall deadline:
 * only reaching the API is time-limited. Fixtures have no live source, so the UI falls back to polling.
 */
async function streamThrough(
  segments: string[],
  search: URLSearchParams,
  { lastEventId, signal }: ReadOptions,
): Promise<Response> {
  if (search.toString().length > MAX_QUERY_CHARS)
    return envelope(414, "QUERY_TOO_LONG", "Query string too long.");
  if (process.env.ABB_WEB_DATA_SOURCE === "fixtures") {
    return envelope(404, "STREAM_NOT_AVAILABLE", "Fixture data has no live stream.");
  }
  const key = process.env.ABB_WEB_API_KEY;
  if (!key) {
    return envelope(503, "WEB_NOT_CONFIGURED", "The web server has no ABB_WEB_API_KEY configured.");
  }
  const base = process.env.ABB_API_INTERNAL_URL ?? "http://localhost:8000";
  const qs = search.toString();
  const headers: Record<string, string> = {
    authorization: `Bearer ${key}`,
    accept: "text/event-stream",
  };
  if (lastEventId && LAST_EVENT_ID.test(lastEventId)) headers["last-event-id"] = lastEventId;

  const connect = new AbortController();
  const timer = setTimeout(() => connect.abort(), TIMEOUT_MS);
  const anySignal = signal ? AbortSignal.any([connect.signal, signal]) : connect.signal;
  try {
    const upstream = await fetch(`${base}/${segments.join("/")}${qs ? `?${qs}` : ""}`, {
      headers,
      cache: "no-store",
      signal: anySignal,
    });
    clearTimeout(timer); // connected: from here on the stream may run for as long as the API allows
    if (upstream.status === 401 || upstream.status === 403) {
      await upstream.body?.cancel();
      return envelope(
        502,
        "WEB_UPSTREAM_AUTH",
        "The web server's API key was rejected by the API.",
      );
    }
    const type = upstream.headers.get("content-type") ?? "";
    if (!upstream.ok || !type.startsWith("text/event-stream")) {
      // An API error (404, 429 STREAM_LIMIT, 503...) is a normal JSON envelope: relay it unchanged.
      const out = new Headers({ "content-type": "application/json", ...SAFE });
      for (const name of ["x-request-id", "retry-after"]) {
        const value = upstream.headers.get(name);
        if (value) out.set(name, value);
      }
      return new Response(upstream.body, { status: upstream.status, headers: out });
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
