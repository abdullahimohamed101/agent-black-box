import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BASE_HEADERS, HSTS, ROUTE_CSP, newNonce, pageCsp } from "@/server/csp";
import { proxy } from "@/proxy";

const directive = (csp: string, name: string) =>
  csp.split("; ").find((d) => d.startsWith(`${name} `)) ?? "";

describe("pageCsp", () => {
  const csp = pageCsp({ nonce: "abc123==" });
  it("allows scripts only with the nonce: no unsafe-inline, no unsafe-eval", () => {
    expect(directive(csp, "script-src")).toBe(
      "script-src 'self' 'nonce-abc123==' 'strict-dynamic'",
    );
  });
  it("styles: own files and nonce'd elements; inline only as attributes", () => {
    expect(directive(csp, "style-src")).toBe("style-src 'self' 'nonce-abc123=='");
    expect(directive(csp, "style-src-attr")).toBe("style-src-attr 'unsafe-inline'");
    expect(csp).not.toMatch(/style-src 'self'[^;]*unsafe-inline/);
  });
  it("everything else is same-origin or off", () => {
    expect(csp).toContain("default-src 'self'");
    expect(directive(csp, "connect-src")).toBe("connect-src 'self'");
    expect(directive(csp, "font-src")).toBe("font-src 'self'");
    expect(directive(csp, "img-src")).toBe("img-src 'self'");
    for (const d of [
      "frame-ancestors 'none'",
      "object-src 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ])
      expect(csp).toContain(d);
    expect(csp).not.toMatch(/https?:\/\//);
  });
  it("eval only in development; upgrade-insecure-requests only over https", () => {
    expect(csp).not.toContain("unsafe-eval");
    expect(csp).not.toContain("upgrade-insecure-requests");
    expect(pageCsp({ nonce: "n", dev: true })).toContain("'unsafe-eval'");
    expect(pageCsp({ nonce: "n", https: true })).toContain("upgrade-insecure-requests");
  });
});

describe("newNonce", () => {
  it("is base64, 128 bits and never repeats", () => {
    const seen = new Set(Array.from({ length: 200 }, newNonce));
    expect(seen.size).toBe(200);
    for (const nonce of seen) expect(nonce).toMatch(/^[A-Za-z0-9+/]{22}==$/);
  });
});

describe("proxy headers", () => {
  beforeEach(() => vi.stubEnv("ABB_WEB_DATA_SOURCE", "fixtures"));
  afterEach(() => vi.unstubAllEnvs());

  it("gives a page a fresh nonce in the response and in the request header Next reads", () => {
    const first = proxy(new NextRequest("http://localhost/login"));
    const second = proxy(new NextRequest("http://localhost/login"));
    const nonce = (res: Response) =>
      /'nonce-([^']+)'/.exec(res.headers.get("content-security-policy") ?? "")![1];
    expect(nonce(first)).not.toBe(nonce(second));
    // NextResponse.next({request: {headers}}) is conveyed to Next through x-middleware-request-* headers
    expect(first.headers.get("x-middleware-request-content-security-policy")).toBe(
      first.headers.get("content-security-policy"),
    );
    expect(first.headers.get("x-middleware-request-x-nonce")).toBe(nonce(first));
  });
  it("sends the base headers on every response and the deny-everything policy on routes", () => {
    const page = proxy(new NextRequest("http://localhost/login"));
    const route = proxy(new NextRequest("http://localhost/api/abb/v1/me"));
    for (const res of [page, route])
      for (const [name, value] of Object.entries(BASE_HEADERS))
        expect(res.headers.get(name)).toBe(value);
    expect(route.headers.get("content-security-policy")).toBe(ROUTE_CSP);
  });
  it("sends HSTS only when the deployment's origin is https", () => {
    expect(
      proxy(new NextRequest("http://localhost/login")).headers.get("strict-transport-security"),
    ).toBeNull();
    vi.stubEnv("ABB_WEB_ORIGIN", "http://localhost:3000");
    expect(
      proxy(new NextRequest("http://localhost/login")).headers.get("strict-transport-security"),
    ).toBeNull();
    vi.stubEnv("ABB_WEB_ORIGIN", "https://abb.example");
    const res = proxy(new NextRequest("http://localhost/login"));
    expect(res.headers.get("strict-transport-security")).toBe(HSTS);
    expect(res.headers.get("content-security-policy")).toContain("upgrade-insecure-requests");
  });
  it("the sign-in redirect carries the headers too", () => {
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "api");
    const res = proxy(new NextRequest("http://localhost/w/acme"));
    expect(res.status).toBe(307);
    expect(res.headers.get("content-security-policy")).toBe(ROUTE_CSP);
    expect(res.headers.get("x-frame-options")).toBe("DENY");
  });
});
