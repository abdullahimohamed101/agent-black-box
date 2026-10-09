import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { callbackRoute, loginRoute, logoutRoute } from "@/server/authRoutes";
import { LoginLimiter, setLoginLimiter } from "@/server/loginLimiter";

const ORIGIN = "http://localhost:3000";
const SESSION = "s".repeat(43);
const STATE = "t".repeat(43);

beforeEach(() => {
  vi.stubEnv("ABB_WEB_ORIGIN", ORIGIN);
  vi.stubEnv("ABB_API_INTERNAL_URL", "http://api.internal");
  vi.stubEnv("ABB_WEB_DATA_SOURCE", "api");
  setLoginLimiter(new LoginLimiter());
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  setLoginLimiter(undefined);
});

function upstream(status: number, headers: [string, string][] = []) {
  const h = new Headers();
  for (const [k, v] of headers) h.append(k, v);
  return new Response(null, { status, headers: h });
}
function stubFetch(res: Response | Error) {
  const fetchMock = vi.fn(async () => {
    if (res instanceof Error) throw res;
    return res;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
const get = (path: string, headers: Record<string, string> = {}) =>
  new Request(`${ORIGIN}${path}`, { headers });
const post = (path: string, headers: Record<string, string> = {}) =>
  new Request(`${ORIGIN}${path}`, { method: "POST", headers });

describe("GET /api/auth/login", () => {
  it("relays the identity provider redirect and the login cookie without following it", async () => {
    const cookie = `abb_login=${STATE}; Max-Age=600; Path=/api/auth; HttpOnly; SameSite=Lax`;
    const fetchMock = stubFetch(
      upstream(302, [
        ["location", "https://idp.example/authorize?client_id=abb&state=x"],
        ["set-cookie", cookie],
      ]),
    );
    const res = await loginRoute(get("/api/auth/login?return_to=%2Fw%2Facme%2Fprojects%2Fall"));
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("https://idp.example/authorize?client_id=abb&state=x");
    expect(res.headers.getSetCookie()).toEqual([cookie]);
    const [url, init] = fetchMock.mock.calls[0]! as unknown as [string, RequestInit];
    expect(url).toBe("http://api.internal/v1/auth/login?return_to=%2Fw%2Facme%2Fprojects%2Fall");
    expect(init.redirect).toBe("manual");
    expect(init.signal).toBeInstanceOf(AbortSignal); // a deadline on every call
  });

  it("never forwards a return_to that leaves the app (open-redirect guard)", async () => {
    for (const bad of [
      "https://evil.example",
      "//evil.example",
      "/\\evil.example",
      "/%2F%2Fevil",
      "/w/x%0d%0aSet-Cookie:a=b",
      "javascript:alert(1)",
      "/login",
      "/w/a#frag",
    ]) {
      const fetchMock = stubFetch(upstream(302, [["location", "https://idp.example/a"]]));
      await loginRoute(get(`/api/auth/login?return_to=${encodeURIComponent(bad)}`));
      const [url] = fetchMock.mock.calls[0]! as unknown as [string];
      expect(url, bad).toBe("http://api.internal/v1/auth/login");
    }
  });

  it("refuses a Location that is not an absolute http(s) URL, or carries credentials", async () => {
    for (const location of ["/w/acme", "javascript:alert(1)", "https://user:pw@idp.example/a"]) {
      stubFetch(upstream(302, [["location", location]]));
      const res = await loginRoute(get("/api/auth/login"));
      expect(res.headers.get("location"), location).toBe("/login?error=login_failed");
    }
  });

  it("does not relay cookies it did not issue", async () => {
    stubFetch(
      upstream(302, [
        ["location", "https://idp.example/a"],
        ["set-cookie", "tracker=1; Path=/"],
        ["set-cookie", `abb_login=${STATE}; Path=/api/auth; HttpOnly`],
      ]),
    );
    const res = await loginRoute(get("/api/auth/login"));
    expect(res.headers.getSetCookie()).toHaveLength(1);
    expect(res.headers.getSetCookie()[0]).toMatch(/^abb_login=/);
  });

  it("maps API failures to fixed sign-in messages and sets no cookie", async () => {
    for (const [status, code] of [
      [429, "rate_limited"],
      [503, "not_configured"],
      [400, "login_failed"],
    ] as const) {
      stubFetch(upstream(status));
      const res = await loginRoute(get("/api/auth/login"));
      expect(res.headers.get("location")).toBe(`/login?error=${code}`);
      expect(res.headers.getSetCookie()).toEqual([]);
    }
    stubFetch(new Error("down"));
    expect((await loginRoute(get("/api/auth/login"))).headers.get("location")).toBe(
      "/login?error=unavailable",
    );
  });

  it("limits sign-in starts per client address and tells the browser when to retry", async () => {
    setLoginLimiter(new LoginLimiter(2, 100));
    const fetchMock = stubFetch(upstream(302, [["location", "https://idp.example/a"]]));
    const from = (ip: string) => get("/api/auth/login", { "x-forwarded-for": ip });
    expect((await loginRoute(from("1.1.1.1"))).status).toBe(302);
    expect((await loginRoute(from("1.1.1.1"))).headers.get("location")).toBe(
      "https://idp.example/a",
    );
    const limited = await loginRoute(from("1.1.1.1"));
    expect(limited.headers.get("location")).toBe("/login?error=rate_limited");
    expect(Number(limited.headers.get("retry-after"))).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledTimes(2); // the third never reached the API
    expect((await loginRoute(from("2.2.2.2"))).headers.get("location")).toBe(
      "https://idp.example/a",
    );
  });

  it("is bounded for everyone together when addresses rotate", async () => {
    setLoginLimiter(new LoginLimiter(5, 3));
    stubFetch(upstream(302, [["location", "https://idp.example/a"]]));
    const results: (string | null)[] = [];
    for (let i = 0; i < 6; i++) {
      const res = await loginRoute(get("/api/auth/login", { "x-forwarded-for": `9.9.9.${i}` }));
      results.push(res.headers.get("location"));
    }
    expect(results.filter((l) => l === "/login?error=rate_limited")).toHaveLength(3);
  });

  it("does nothing in fixture mode", async () => {
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "fixtures");
    const fetchMock = stubFetch(upstream(302));
    expect((await loginRoute(get("/api/auth/login"))).headers.get("location")).toBe("/");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("GET /api/auth/callback", () => {
  const sessionCookie = `abb_session=${SESSION}; Max-Age=604800; Path=/; HttpOnly; SameSite=Lax`;
  const clearLogin = "abb_login=; Max-Age=0; Path=/api/auth; HttpOnly; SameSite=Lax";

  it("relays Set-Cookie (session and cleared login cookie) and the validated Location", async () => {
    const fetchMock = stubFetch(
      upstream(302, [
        ["location", "/w/acme/projects/all"],
        ["set-cookie", clearLogin],
        ["set-cookie", sessionCookie],
      ]),
    );
    const res = await callbackRoute(
      get("/api/auth/callback?code=abc&state=xyz", {
        cookie: `abb_login=${STATE}; abb_session=${SESSION}; other=1`,
      }),
    );
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/w/acme/projects/all");
    expect(res.headers.getSetCookie()).toEqual([clearLogin, sessionCookie]);
    const [url, init] = fetchMock.mock.calls[0]! as unknown as [string, RequestInit];
    expect(url).toBe("http://api.internal/v1/auth/callback?code=abc&state=xyz");
    expect(init.redirect).toBe("manual");
    // Only the login cookie goes to the API on the callback: not the session, not unrelated cookies.
    expect((init.headers as Record<string, string>).cookie).toBe(`abb_login=${STATE}`);
  });

  it("falls back to / for a Location that is not an app path", async () => {
    for (const location of ["https://evil.example/", "//evil.example", "/\\evil", "/w/a%0d%0ax"]) {
      stubFetch(upstream(302, [["location", location]]));
      const res = await callbackRoute(get("/api/auth/callback?code=a&state=b"));
      expect(res.headers.get("location"), location).toBe("/");
    }
  });

  it("sends a failed sign-in to /login and still relays the cleared login cookie, never a session", async () => {
    stubFetch(upstream(400, [["set-cookie", clearLogin]]));
    const res = await callbackRoute(get("/api/auth/callback?code=a&state=b"));
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/login?error=login_failed");
    expect(res.headers.getSetCookie()).toEqual([clearLogin]);
    stubFetch(upstream(401, [["set-cookie", clearLogin]]));
    const again = await callbackRoute(get("/api/auth/callback?code=a&state=b"));
    expect(again.headers.getSetCookie().join(";")).not.toContain("abb_session");
  });

  it("forwards only code, state and error, within bounds", async () => {
    const fetchMock = stubFetch(upstream(400));
    await callbackRoute(
      get(`/api/auth/callback?code=a&state=b&error=denied&extra=1&state2=${"x".repeat(10)}`),
    );
    expect((fetchMock.mock.calls[0]! as unknown as [string])[0]).toBe(
      "http://api.internal/v1/auth/callback?code=a&state=b&error=denied",
    );
    const big = stubFetch(upstream(400));
    await callbackRoute(get(`/api/auth/callback?code=${"c".repeat(3000)}&state=b`));
    expect((big.mock.calls[0]! as unknown as [string])[0]).toBe(
      "http://api.internal/v1/auth/callback?state=b",
    );
  });

  it("is rate limited like the login start", async () => {
    setLoginLimiter(new LoginLimiter(1, 100));
    const fetchMock = stubFetch(upstream(400));
    await callbackRoute(get("/api/auth/callback?code=a&state=b"));
    const second = await callbackRoute(get("/api/auth/callback?code=a&state=b"));
    expect(second.headers.get("location")).toBe("/login?error=rate_limited");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("POST /api/auth/logout", () => {
  const clearSession = "abb_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax";

  it("revokes at the API with the session cookie and the exact origin, relays the cleared cookie", async () => {
    const fetchMock = stubFetch(upstream(200, [["set-cookie", clearSession]]));
    const res = await logoutRoute(
      post("/api/auth/logout", { origin: ORIGIN, cookie: `abb_session=${SESSION}; other=1` }),
    );
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe("/login");
    expect(res.headers.getSetCookie()).toEqual([clearSession]);
    const [url, init] = fetchMock.mock.calls[0]! as unknown as [string, RequestInit];
    expect(url).toBe("http://api.internal/v1/auth/logout");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ origin: ORIGIN, cookie: `abb_session=${SESSION}` });
  });

  it("rejects a missing, foreign, null or look-alike Origin before calling the API", async () => {
    const fetchMock = stubFetch(upstream(200));
    for (const origin of [
      undefined,
      "null",
      "https://evil.example",
      "http://localhost:3001",
      "https://localhost:3000",
      `${ORIGIN}.evil.example`,
    ]) {
      const res = await logoutRoute(
        post("/api/auth/logout", {
          cookie: `abb_session=${SESSION}`,
          ...(origin ? { origin } : {}),
        }),
      );
      expect(res.status, String(origin)).toBe(403);
      expect(((await res.json()) as { error: { code: string } }).error.code).toBe("CSRF_REJECTED");
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("is idempotent without a session and reports an unreachable API instead of pretending", async () => {
    const fetchMock = stubFetch(upstream(200));
    const none = await logoutRoute(post("/api/auth/logout", { origin: ORIGIN }));
    expect(none.status).toBe(303);
    expect(fetchMock).not.toHaveBeenCalled();
    stubFetch(new Error("down"));
    const down = await logoutRoute(
      post("/api/auth/logout", { origin: ORIGIN, cookie: `abb_session=${SESSION}` }),
    );
    expect(down.status).toBe(503);
    expect(down.headers.getSetCookie()).toEqual([]); // the cookie stays: the session was not revoked
  });

  it("needs ABB_WEB_ORIGIN", async () => {
    vi.stubEnv("ABB_WEB_ORIGIN", "");
    expect((await logoutRoute(post("/api/auth/logout", { origin: ORIGIN }))).status).toBe(503);
  });

  it("uses the __Host- cookie name under https", async () => {
    vi.stubEnv("ABB_WEB_ORIGIN", "https://abb.example");
    const clear = "__Host-abb_session=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax";
    const fetchMock = stubFetch(upstream(200, [["set-cookie", clear]]));
    const res = await logoutRoute(
      post("/api/auth/logout", {
        origin: "https://abb.example",
        cookie: `__Host-abb_session=${SESSION}`,
      }),
    );
    expect(res.headers.getSetCookie()).toEqual([clear]);
    expect(
      (
        (fetchMock.mock.calls[0]! as unknown as [string, RequestInit])[1].headers as Record<
          string,
          string
        >
      ).cookie,
    ).toBe(`__Host-abb_session=${SESSION}`);
  });
});
