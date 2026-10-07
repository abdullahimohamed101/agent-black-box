import { fixtureReply } from "@/fixtures/api";

/** Read paths the browser may reach (ADR-021). Anything else is a 404, even though the API would reject writes anyway. */
const ALLOWED = /^v1\/runs(\/[A-Za-z0-9_-]{1,64}(\/(spans|events(\/[A-Za-z0-9_-]{1,64})?))?)?$/;
const TIMEOUT_MS = 10_000;
const MAX_QUERY_CHARS = 2048;
// Proxy responses carry tenant data: never cacheable, never sniffed.
const SAFE = { "cache-control": "no-store", "x-content-type-options": "nosniff" } as const;

const envelope = (status: number, code: string, message: string, retryable = false) =>
  Response.json(
    { error: { code, message, category: "WEB", retryable, request_id: null, details: {} } },
    { status, headers: SAFE },
  );

export async function readThrough(segments: string[], search: URLSearchParams): Promise<Response> {
  if (!ALLOWED.test(segments.join("/"))) return envelope(404, "NOT_FOUND", "Not found.");
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
