import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { safeReturnTo } from "@/server/config";
import { loginRedirect } from "@/server/gate";
import { LoginLimiter, clientKey } from "@/server/loginLimiter";
import { fetchMe, resolveWorkspace } from "@/server/session";

const SESSION = "s".repeat(43);
const COOKIE = `abb_session=${SESSION}`;
const WS_A = "ws_00000000000000000000000001";
const WS_B = "ws_00000000000000000000000002";

beforeEach(() => {
  vi.stubEnv("ABB_WEB_ORIGIN", "http://localhost:3000");
  vi.stubEnv("ABB_API_INTERNAL_URL", "http://api.internal");
  vi.stubEnv("ABB_WEB_DATA_SOURCE", "api");
  vi.stubEnv("ABB_WEB_API_KEY", "");
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("safeReturnTo (open-redirect guard)", () => {
  it("keeps app paths", () => {
    for (const ok of [
      "/",
      "/w/acme/projects/all",
      "/w/acme/projects/all/runs?status=FAILED",
      "/invite",
    ])
      expect(safeReturnTo(ok), ok).toBe(ok);
  });
  it("drops everything else", () => {
    for (const bad of [
      "https://evil.example",
      "//evil.example",
      "/\\evil.example",
      "/%2F%2Fevil",
      "/w/x%0d%0a",
      "javascript:alert(1)",
      "/login",
      "/api/auth/logout",
      "/w/a#frag",
      "w/relative",
      "",
      null,
      undefined,
      `/w/${"a".repeat(500)}`,
    ])
      expect(safeReturnTo(bad as string), String(bad)).toBeNull();
  });
});

describe("loginRedirect (proxy.ts fast path)", () => {
  it("sends a visitor without a session cookie to /login with a safe return_to", () => {
    expect(loginRedirect("/w/acme/projects/all", "", null)).toBe(
      "/login?return_to=%2Fw%2Facme%2Fprojects%2Fall",
    );
    expect(loginRedirect("/w/acme/projects/all/runs", "?status=FAILED", "other=1")).toBe(
      "/login?return_to=%2Fw%2Facme%2Fprojects%2Fall%2Fruns%3Fstatus%3DFAILED",
    );
  });
  it("drops a return_to it cannot vouch for, and lets a cookie holder through to the real check", () => {
    expect(loginRedirect("/w/acme/projects/all", "?x=%2F%2Fevil", null)).toBe(
      "/login?return_to=%2Fw%2Facme%2Fprojects%2Fall",
    );
    expect(loginRedirect("/w/a b", "", null)).toBe("/login");
    expect(loginRedirect("/w/acme", "", COOKIE)).toBeNull();
    expect(loginRedirect("/w/acme", "", "abb_session=short")).not.toBeNull();
  });
  it("stands aside in fixture mode and while the legacy key exists", () => {
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "fixtures");
    expect(loginRedirect("/w/acme", "", null)).toBeNull();
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "api");
    vi.stubEnv("ABB_WEB_API_KEY", "abb_live_k.s");
    expect(loginRedirect("/w/acme", "", null)).toBeNull();
  });
});

const me = (
  memberships: { slug: string; id: string; role?: string; permissions?: string[] }[],
) => ({
  user: { id: "usr_1", email: "a@b.co", name: null },
  memberships: memberships.map((m) => ({
    workspace: { id: m.id, slug: m.slug, name: m.slug.toUpperCase() },
    role: m.role ?? "VIEWER",
    permissions: m.permissions ?? ["run.read"],
    own_permissions: [],
  })),
});

function stubApi(routes: Record<string, () => Response>) {
  const calls: { url: string; headers: Record<string, string> }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, headers: init.headers as Record<string, string> });
      const hit = routes[new URL(url).pathname];
      return hit ? hit() : new Response("{}", { status: 404 });
    }),
  );
  return calls;
}

describe("workspace resolution (KI-027)", () => {
  it("resolves the slug through /v1/me and lists projects for that workspace id", async () => {
    const calls = stubApi({
      "/v1/me": () =>
        Response.json(
          me([
            { slug: "acme", id: WS_A, role: "OWNER", permissions: ["member.write"] },
            { slug: "globex", id: WS_B },
          ]),
        ),
      "/v1/projects": () => Response.json({ items: [{ id: "prj_1", slug: "web", name: "Web" }] }),
    });
    const r = await resolveWorkspace("acme", COOKIE);
    expect(r.status).toBe("ok");
    if (r.status !== "ok") return;
    expect(r.value.workspace).toEqual({ id: WS_A, slug: "acme", name: "ACME" });
    expect(r.value.role).toBe("OWNER");
    expect(r.value.permissions).toEqual(["member.write"]);
    expect(r.value.projects).toEqual([{ id: "prj_1", slug: "web", name: "Web" }]);
    expect(r.value.memberships.map((m) => m.slug)).toEqual(["acme", "globex"]);
    expect(calls[1]!.headers["x-abb-workspace"]).toBe(WS_A);
    expect(calls.every((c) => c.headers.cookie === COOKIE)).toBe(true);
  });

  it("treats a workspace the person does not belong to like one that does not exist", async () => {
    stubApi({ "/v1/me": () => Response.json(me([{ slug: "acme", id: WS_A }])) });
    expect((await resolveWorkspace("globex", COOKIE)).status).toBe("not_found");
  });

  it("reports signed-out, expired and unavailable distinctly", async () => {
    expect((await resolveWorkspace("acme", null)).status).toBe("unauthenticated");
    stubApi({ "/v1/me": () => Response.json({ error: {} }, { status: 401 }) });
    expect((await resolveWorkspace("acme", COOKIE)).status).toBe("unauthenticated");
    stubApi({ "/v1/me": () => new Response("{}", { status: 503 }) });
    expect((await resolveWorkspace("acme", COOKIE)).status).toBe("unavailable");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
    expect((await fetchMe(COOKIE)).status).toBe("unavailable");
  });

  it("uses the legacy key when there is no session, without a person", async () => {
    vi.stubEnv("ABB_WEB_API_KEY", "abb_live_k.secret");
    const calls = stubApi({
      "/v1/projects": () => Response.json({ items: [{ id: "prj_1", slug: "web", name: "Web" }] }),
    });
    const r = await resolveWorkspace("whatever", null);
    expect(r.status).toBe("ok");
    if (r.status !== "ok") return;
    expect(r.value).toMatchObject({
      mode: "key",
      user: null,
      permissions: [],
      workspace: { id: null },
    });
    expect(r.value.projects).toHaveLength(1);
    expect(calls[0]!.headers.authorization).toBe("Bearer abb_live_k.secret");
  });

  it("serves a fixture person in fixture mode and refuses it in production", async () => {
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "fixtures");
    const r = await resolveWorkspace("default", null);
    expect(r.status).toBe("ok");
    if (r.status === "ok") expect(r.value).toMatchObject({ mode: "fixtures", role: "OWNER" });
    const viewer = await resolveWorkspace("viewer-only", null);
    if (viewer.status === "ok") expect(viewer.value.role).toBe("VIEWER");
    vi.stubEnv("NODE_ENV", "production");
    expect((await resolveWorkspace("default", null)).status).toBe("unavailable");
  });
});

describe("login limiter", () => {
  it("refills over time and forgets the oldest client when full", () => {
    let now = 0;
    const limiter = new LoginLimiter(2, 1000, 2, () => now);
    expect(limiter.acquire("a")).toBeNull();
    expect(limiter.acquire("a")).toBeNull();
    expect(limiter.acquire("a")).toBeGreaterThan(0);
    now += 31_000; // half a minute buys one token at 2/min
    expect(limiter.acquire("a")).toBeNull();
    limiter.acquire("b");
    limiter.acquire("c"); // "a" is evicted: bounded memory
    expect(limiter.acquire("a")).toBeNull();
  });
  it("keys on the first forwarded address and shares one bucket when there is none", () => {
    expect(clientKey(new Headers({ "x-forwarded-for": "1.2.3.4, 10.0.0.1" }))).toBe("1.2.3.4");
    expect(clientKey(new Headers())).toBe("unknown");
    expect(clientKey(new Headers({ "x-forwarded-for": "x".repeat(200) }))).toBe("unknown");
  });
});
