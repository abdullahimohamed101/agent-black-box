import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SignOutButton } from "@/components/SignOutButton";
import { config, proxy } from "@/proxy";

beforeEach(() => {
  vi.stubEnv("ABB_WEB_ORIGIN", "https://abb.example");
  vi.stubEnv("ABB_WEB_DATA_SOURCE", "api");
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("proxy.ts", () => {
  it("runs for pages and routes (the policy is per response) but not for Next's static assets", () => {
    // Phase 19a: this was ["/w/:path*"] when the proxy only gated workspace pages. It now also sets the CSP, so the
    // login gate itself checks the path (see the next test).
    const [rule] = config.matcher as { source: string }[];
    const re = new RegExp(`^${rule!.source}$`);
    for (const hit of ["/", "/login", "/w/acme", "/api/abb/v1/me", "/invite"])
      expect(re.test(hit)).toBe(true);
    for (const miss of ["/_next/static/chunks/a.js", "/_next/image", "/favicon.ico", "/icon.svg"])
      expect(re.test(miss)).toBe(false);
  });
  it("gates only workspace pages: /login and /invite are never redirected", () => {
    for (const path of ["/login", "/invite", "/", "/api/health"]) {
      const res = proxy(new NextRequest(`http://localhost${path}`));
      expect(res.headers.get("location")).toBeNull();
    }
  });
  it("redirects a cookieless visitor to an absolute /login URL on the configured origin, not the Host header", () => {
    const res = proxy(new NextRequest("http://evil.example/w/acme/projects/all"));
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe(
      "https://abb.example/login?return_to=%2Fw%2Facme%2Fprojects%2Fall",
    );
  });
  it("lets a visitor with a session cookie through to the real check", () => {
    const res = proxy(
      new NextRequest("http://localhost/w/acme", {
        headers: { cookie: `__Host-abb_session=${"s".repeat(43)}` },
      }),
    );
    expect(res.headers.get("location")).toBeNull();
  });
});

describe("SignOutButton", () => {
  it("POSTs with fetch (a form would send Origin: null under no-referrer) and reports failure", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<SignOutButton />);
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Sign-out failed"));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/auth/logout");
    expect(init).toMatchObject({ method: "POST", redirect: "manual" });
  });
});
