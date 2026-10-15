import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetFixtureQuotas } from "@/fixtures/quotas";
import { readThrough } from "@/server/upstream";

const ORIGIN = "http://localhost:3000";
const SESSION = "s".repeat(43);
const PRJ = "prj_00000000000000000000000001";
const body = (text: string) =>
  new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(new TextEncoder().encode(text));
      c.close();
    },
  });
const headers = (extra: Record<string, string> = {}) =>
  new Headers({
    cookie: `abb_session=${SESSION}`,
    origin: ORIGIN,
    "content-type": "application/json",
    ...extra,
  });

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("quota routes through the proxy (API mode)", () => {
  beforeEach(() => {
    vi.stubEnv("ABB_WEB_ORIGIN", ORIGIN);
    vi.stubEnv("ABB_API_INTERNAL_URL", "http://api.internal");
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "api");
  });

  it("forwards the read as GET and the override as PUT with the session only", async () => {
    const f = vi.fn(async () => Response.json({ quotas: [] }));
    vi.stubGlobal("fetch", f);
    for (const method of ["GET", "PUT"] as const) {
      const r = await readThrough(["v1", "projects", PRJ, "quotas"], new URLSearchParams(), {
        method,
        headers: headers({ authorization: "Bearer nope", "x-forwarded-for": "1.2.3.4" }),
        body: method === "PUT" ? body('{"agents":10}') : null,
      });
      expect(r.status, method).toBe(200);
    }
    const put = f.mock.calls.at(-1) as unknown as [string, RequestInit];
    expect(put[0]).toBe(`http://api.internal/v1/projects/${PRJ}/quotas`);
    expect(put[1].method).toBe("PUT");
    expect(put[1].body).toBe('{"agents":10}');
    const sent = put[1].headers as Record<string, string>;
    expect(sent.origin).toBe(ORIGIN);
    expect(Object.keys(sent).sort()).not.toContain("authorization");
    expect(Object.keys(sent).sort()).not.toContain("x-forwarded-for");
  });

  it("refuses a cross-origin PUT, a PUT without a JSON body and every other method", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({})),
    );
    const put = (h: Headers) =>
      readThrough(["v1", "projects", PRJ, "quotas"], new URLSearchParams(), {
        method: "PUT",
        headers: h,
        body: body("{}"),
      });
    expect((await put(headers({ origin: "http://evil.test" }))).status).toBe(403);
    expect((await put(headers({ "content-type": "text/plain" }))).status).toBe(415);
    for (const method of ["POST", "DELETE", "PATCH"] as const) {
      const r = await readThrough(["v1", "projects", PRJ, "quotas"], new URLSearchParams(), {
        method,
        headers: headers(),
        body: body("{}"),
      });
      expect(r.status, method).toBe(405);
    }
  });

  it("does not carry a quota path with extra segments or a malformed id", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({})),
    );
    for (const path of [
      ["v1", "projects", PRJ, "quotas", "extra"],
      ["v1", "projects", "a/b", "quotas"],
      ["v1", "projects", "x".repeat(65), "quotas"],
      ["v1", "quotas"],
    ]) {
      const r = await readThrough(path, new URLSearchParams(), { headers: headers() });
      expect(r.status, path.join("/")).toBe(404);
    }
  });
});

describe("quota routes in fixtures mode", () => {
  beforeEach(() => {
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "fixtures");
    resetFixtureQuotas();
  });

  const put = (payload: string, extra: Record<string, string> = {}) =>
    readThrough(["v1", "projects", PRJ, "quotas"], new URLSearchParams(), {
      method: "PUT",
      headers: new Headers({ "content-type": "application/json", ...extra }),
      body: body(payload),
    });
  const get = async (extra: Record<string, string> = {}) =>
    readThrough(["v1", "projects", PRJ, "quotas"], new URLSearchParams(), {
      headers: new Headers(extra),
    });

  it("serves usage with every status and keeps overrides in memory", async () => {
    const first = (await (await get()).json()) as {
      quotas: { quota: string; status: string; limit: number }[];
    };
    expect(first.quotas.map((q) => [q.quota, q.status])).toEqual([
      ["agents", "ok"],
      ["runs_per_day", "near_limit"],
      ["events_per_day", "at_limit"],
      ["artifact_bytes", "ok"],
    ]);
    expect((await put('{"events_per_day":9000000}')).status).toBe(200);
    const after = (await (await get()).json()) as typeof first;
    expect(after.quotas.find((q) => q.quota === "events_per_day")).toMatchObject({
      limit: 9_000_000,
      status: "ok",
    });
    expect((await put('{"events_per_day":null}')).status).toBe(200);
    resetFixtureQuotas();
  });

  it("validates like the API and refuses a viewer's write", async () => {
    for (const bad of [
      '{"agents":0}',
      '{"agents":"5"}',
      '{"agents":1.5}',
      '{"nope":1}',
      "not json",
    ])
      expect((await put(bad)).status, bad).toBe(bad === "not json" ? 422 : 422);
    const viewer = { "x-abb-workspace": "ws_00000000000000000000000002" };
    expect((await put('{"agents":5}', viewer)).status).toBe(403);
    expect((await get(viewer)).status).toBe(200); // every member reads usage
  });
});
