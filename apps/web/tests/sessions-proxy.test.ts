import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetFixtureSessions } from "@/fixtures/sessions";
import { readThrough } from "@/server/upstream";

const ORIGIN = "http://localhost:3000";
const SESSION = "s".repeat(43);
const SID = "00000000-0000-4000-8000-000000000002";
const headers = (extra: Record<string, string> = {}) =>
  new Headers({ cookie: `abb_session=${SESSION}`, origin: ORIGIN, ...extra });

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("session routes through the proxy (API mode)", () => {
  beforeEach(() => {
    vi.stubEnv("ABB_WEB_ORIGIN", ORIGIN);
    vi.stubEnv("ABB_API_INTERNAL_URL", "http://api.internal");
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "api");
  });

  it("forwards the list, one revoke and revoke-others with the session cookie and nothing else", async () => {
    const f = vi.fn(async () => Response.json({ items: [] }));
    vi.stubGlobal("fetch", f);
    const cases = [
      ["GET", ["v1", "auth", "sessions"]],
      ["DELETE", ["v1", "auth", "sessions", SID]],
      ["POST", ["v1", "auth", "sessions", "revoke-others"]],
    ] as const;
    for (const [method, path] of cases) {
      const r = await readThrough([...path], new URLSearchParams(), {
        method,
        headers: headers({ authorization: "Bearer nope", "x-forwarded-for": "1.2.3.4" }),
      });
      expect(r.status, method).toBe(200);
    }
    const calls = f.mock.calls as unknown as [string, RequestInit][];
    expect(calls.map(([url, init]) => [init.method, url])).toEqual([
      ["GET", "http://api.internal/v1/auth/sessions"],
      ["DELETE", `http://api.internal/v1/auth/sessions/${SID}`],
      ["POST", "http://api.internal/v1/auth/sessions/revoke-others"],
    ]);
    for (const [, init] of calls) {
      const sent = init.headers as Record<string, string>;
      expect(sent.cookie).toBe(`abb_session=${SESSION}`);
      expect(Object.keys(sent)).not.toContain("authorization");
      expect(Object.keys(sent)).not.toContain("x-forwarded-for");
    }
    const writes = calls.slice(1).map(([, init]) => init);
    for (const w of writes) {
      expect((w.headers as Record<string, string>).origin).toBe(ORIGIN);
      expect(w.body).toBeUndefined(); // no body goes upstream
    }
  });

  it("refuses a cross-origin write and a write without a session, and every other method", async () => {
    const f = vi.fn(async () => Response.json({}));
    vi.stubGlobal("fetch", f);
    const revoke = (
      h: Headers,
      method = "POST",
      path = ["v1", "auth", "sessions", "revoke-others"],
    ) => readThrough(path, new URLSearchParams(), { method, headers: h });
    expect((await revoke(headers({ origin: "http://evil.test" }))).status).toBe(403);
    expect((await revoke(new Headers({ origin: ORIGIN }))).status).toBe(401);
    expect(
      (
        await revoke(headers({ origin: "http://evil.test" }), "DELETE", [
          "v1",
          "auth",
          "sessions",
          SID,
        ])
      ).status,
    ).toBe(403);
    for (const method of ["PUT", "PATCH"] as const) {
      expect(
        (await revoke(headers(), method, ["v1", "auth", "sessions", SID])).status,
        method,
      ).toBe(405);
    }
    expect((await revoke(headers(), "POST", ["v1", "auth", "sessions", SID])).status).toBe(405);
    expect(
      (await revoke(headers(), "DELETE", ["v1", "auth", "sessions", "revoke-others"])).status,
    ).toBe(
      200, // `revoke-others` is a well-formed id as far as the proxy goes; the API decides (404)
    );
    f.mockClear();
  });

  it("does not carry malformed session paths", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({})),
    );
    for (const path of [
      ["v1", "auth", "sessions", "a/b"],
      ["v1", "auth", "sessions", "x".repeat(65)],
      ["v1", "auth", "sessions", SID, "extra"],
      ["v1", "auth", "login"],
      ["v1", "auth", "callback"],
      ["v1", "auth", "logout"],
    ]) {
      const r = await readThrough(path, new URLSearchParams(), { headers: headers() });
      expect(r.status, path.join("/")).toBe(404);
    }
  });
});

describe("session routes in fixtures mode", () => {
  beforeEach(() => {
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "fixtures");
    resetFixtureSessions();
  });
  const call = (method: string, ...path: string[]) =>
    readThrough(["v1", "auth", "sessions", ...path], new URLSearchParams(), {
      method,
      headers: new Headers(),
    });

  it("lists, revokes one, refuses the current and unknown ones, then revokes the rest", async () => {
    const first = (await (await call("GET")).json()) as {
      items: { id: string; current: boolean }[];
    };
    expect(first.items.map((s) => s.current)).toEqual([true, false, false]);
    const current = first.items[0]!.id;
    const gone = await call("DELETE", current);
    expect(gone.status).toBe(409);
    expect((await call("DELETE", "00000000-0000-4000-8000-0000000000ff")).status).toBe(404);
    expect((await call("DELETE", SID)).status).toBe(204);
    expect((await call("DELETE", SID)).status).toBe(404); // already ended: the same 404
    const revoked = await call("POST", "revoke-others");
    expect(await revoked.json()).toEqual({ revoked: 1 });
    const after = (await (await call("GET")).json()) as { items: unknown[] };
    expect(after.items).toHaveLength(1);
    resetFixtureSessions();
  });
});
