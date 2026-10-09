export const dynamic = "force-dynamic";

const TIMEOUT_MS = 4_000;
const SAFE = { "cache-control": "no-store", "x-content-type-options": "nosniff" } as const;

/**
 * Readiness for the home page's status panel. The browser may only talk to this origin (CSP `connect-src 'self'`),
 * so the web server asks the API; `/readyz` is unauthenticated and carries no tenant data.
 */
export async function GET(): Promise<Response> {
  if (process.env.ABB_WEB_DATA_SOURCE === "fixtures") {
    return Response.json({ status: "ok", version: "fixtures", database: "ok" }, { headers: SAFE });
  }
  const base = process.env.ABB_API_INTERNAL_URL ?? "http://localhost:8000";
  try {
    const upstream = await fetch(`${base}/readyz`, {
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const headers = new Headers({ "content-type": "application/json", ...SAFE });
    const rid = upstream.headers.get("x-request-id");
    if (rid) headers.set("x-request-id", rid);
    return new Response(await upstream.text(), { status: upstream.status, headers });
  } catch {
    return Response.json(
      {
        error: {
          code: "WEB_UPSTREAM_UNREACHABLE",
          message: "The web server cannot reach the API.",
          request_id: null,
        },
      },
      { status: 502, headers: SAFE },
    );
  }
}
