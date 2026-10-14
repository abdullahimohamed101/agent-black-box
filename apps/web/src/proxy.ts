import { NextResponse, type NextRequest } from "next/server";
import { webOrigin } from "@/server/config";
import { BASE_HEADERS, HSTS, ROUTE_CSP, newNonce, pageCsp } from "@/server/csp";
import { loginRedirect } from "@/server/gate";

// Next 16's request interceptor (formerly middleware). It does two jobs for every page and route request:
//  1. gate workspace pages (/w/...): without a session cookie go to /login at once. /invite handles its own sign-in
//     prompt so the invitation token in its URL fragment survives;
//  2. give the response its Content-Security-Policy. Pages get a fresh nonce per request, which Next reads from the
//     request's CSP header to stamp its own bootstrap scripts (so scripts need no 'unsafe-inline'); this is why every
//     page is rendered per request (`dynamic = "force-dynamic"` in the root layout). Routes under /api render nothing.
// HSTS is sent only when ABB_WEB_ORIGIN is an https origin (the deployment says it is behind TLS). ADR-155.
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const https = webOrigin()?.startsWith("https://") ?? false;
  const edge = (response: NextResponse): NextResponse => {
    for (const [name, value] of Object.entries(BASE_HEADERS)) response.headers.set(name, value);
    if (https) response.headers.set("Strict-Transport-Security", HSTS);
    return response;
  };

  const gated = pathname === "/w" || pathname.startsWith("/w/");
  const to = gated ? loginRedirect(pathname, search, request.headers.get("cookie")) : null;
  if (to) {
    // Next needs an absolute URL here; the configured origin beats the Host header a client could choose.
    const res = NextResponse.redirect(new URL(to, webOrigin() ?? request.url), 307);
    res.headers.set("cache-control", "no-store");
    res.headers.set("Content-Security-Policy", ROUTE_CSP);
    return edge(res);
  }

  if (pathname.startsWith("/api/")) {
    const res = NextResponse.next();
    res.headers.set("Content-Security-Policy", ROUTE_CSP);
    return edge(res);
  }

  const nonce = newNonce();
  const csp = pageCsp({ nonce, https, dev: process.env.NODE_ENV === "development" });
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  const res = NextResponse.next({ request: { headers: requestHeaders } });
  res.headers.set("Content-Security-Policy", csp);
  return edge(res);
}

// Everything except Next's static assets (served with the static headers from next.config.ts) and prefetches.
export const config = {
  matcher: [
    {
      source: "/((?!_next/static|_next/image|favicon.ico|icon.svg).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
