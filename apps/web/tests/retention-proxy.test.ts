import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetFixtureRetention } from "@/fixtures/retention";
import { readThrough } from "@/server/upstream";

const ORIGIN = "http://localhost:3000";
const SESSION = "s".repeat(43);
const WS = "ws_00000000000000000000000001";
const VIEWER_WS = "ws_00000000000000000000000002";
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
const path = (...rest: string[]) => ["v1", "workspaces", WS, "retention", ...rest];

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("retention routes through the proxy (API mode)", () => {
  beforeEach(() => {
    vi.stubEnv("ABB_WEB_ORIGIN", ORIGIN);
    vi.stubEnv("ABB_API_INTERNAL_URL", "http://api.internal");
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "api");
  });

  it("forwards the policy, preview and reports reads and the policy PUT, with the session only", async () => {
    const f = vi.fn(async () => Response.json({ tiers: [] }));
    vi.stubGlobal("fetch", f);
    for (const [rest, method, qs] of [
      [[], "GET", ""],
      [["preview"], "GET", "trace_days=30&run_days=forever"],
      [["reports"], "GET", "limit=10"],
      [[], "PUT", ""],
    ] as const) {
      const r = await readThrough(path(...rest), new URLSearchParams(qs), {
        method,
        headers: headers({ authorization: "Bearer nope", "x-forwarded-for": "1.2.3.4" }),
        body: method === "PUT" ? body('{"trace":30}') : null,
      });
      expect(r.status, `${method} ${rest.join("/")}`).toBe(200);
    }
    const calls = f.mock.calls as unknown as [string, RequestInit][];
    expect(calls.map((c) => c[0])).toEqual([
      `http://api.internal/v1/workspaces/${WS}/retention`,
      `http://api.internal/v1/workspaces/${WS}/retention/preview?trace_days=30&run_days=forever`,
      `http://api.internal/v1/workspaces/${WS}/retention/reports?limit=10`,
      `http://api.internal/v1/workspaces/${WS}/retention`,
    ]);
    const put = calls[3]!;
    expect(put[1].method).toBe("PUT");
    expect(put[1].body).toBe('{"trace":30}');
    const sent = put[1].headers as Record<string, string>;
    expect(sent.origin).toBe(ORIGIN);
    expect(Object.keys(sent)).not.toContain("authorization");
    expect(Object.keys(sent)).not.toContain("x-forwarded-for");
  });

  it("refuses a cross-origin PUT, a PUT without a JSON body, and every other write", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({})),
    );
    const put = (h: Headers) =>
      readThrough(path(), new URLSearchParams(), { method: "PUT", headers: h, body: body("{}") });
    expect((await put(headers({ origin: "http://evil.test" }))).status).toBe(403);
    expect((await put(headers({ "content-type": "text/plain" }))).status).toBe(415);
    for (const rest of [[], ["preview"], ["reports"]])
      for (const method of ["POST", "DELETE", "PATCH"] as const) {
        const r = await readThrough(path(...rest), new URLSearchParams(), {
          method,
          headers: headers(),
          body: body("{}"),
        });
        expect(r.status, `${method} ${rest.join("/")}`).toBe(405);
      }
    // preview and reports are reads only
    for (const rest of [["preview"], ["reports"]]) {
      const r = await readThrough(path(...rest), new URLSearchParams(), {
        method: "PUT",
        headers: headers(),
        body: body("{}"),
      });
      expect(r.status).toBe(405);
    }
  });

  it("does not carry a retention path with extra segments or a malformed id", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({})),
    );
    for (const p of [
      path("preview", "extra"),
      path("other"),
      ["v1", "workspaces", "a/b", "retention"],
      ["v1", "workspaces", "x".repeat(65), "retention"],
      ["v1", "retention"],
    ]) {
      const r = await readThrough(p, new URLSearchParams(), { headers: headers() });
      expect(r.status, p.join("/")).toBe(404);
    }
  });
});

describe("retention routes in fixtures mode", () => {
  beforeEach(() => {
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "fixtures");
    resetFixtureRetention();
  });
  const put = (payload: string, ws = WS) =>
    readThrough(["v1", "workspaces", ws, "retention"], new URLSearchParams(), {
      method: "PUT",
      headers: new Headers({ "content-type": "application/json", "x-abb-workspace": ws }),
      body: body(payload),
    });
  const get = (rest: string[] = [], qs = "") =>
    readThrough(path(...rest), new URLSearchParams(qs), { headers: new Headers() });

  it("serves the policy and keeps edits in memory, validating like the API", async () => {
    const first = (await (await get()).json()) as {
      mode: string;
      tiers: { tier: string; days: number | null; source: string }[];
    };
    expect(first.mode).toBe("dry_run");
    expect(first.tiers.map((t) => [t.tier, t.days, t.source])).toEqual([
      ["artifacts", 30, "default"],
      ["trace", 90, "default"],
      ["run", 365, "default"],
      ["audit", 365, "default"],
    ]);
    expect((await put('{"artifacts":14}')).status).toBe(200);
    const after = (await (await get()).json()) as typeof first;
    expect(after.tiers[0]).toMatchObject({ days: 14, source: "workspace" });
    expect((await put('{"artifacts":3}')).status).toBe(422);
    expect((await put('{"artifacts":"soon"}')).status).toBe(422);
    expect((await put('{"artifacts":120}')).status).toBe(422); // longer than trace detail
    expect((await put('{"run":"forever"}')).status).toBe(200);
    expect((await put("not json")).status).toBe(422);
    expect((await put('{"artifacts":null}')).status).toBe(200);
  });

  it("refuses a write in the viewer workspace and serves a preview and reports", async () => {
    expect((await put('{"trace":60}', VIEWER_WS)).status).toBe(403);
    const preview = (await (await get(["preview"], "trace_days=30&artifacts_days=14")).json()) as {
      proposed: boolean;
      tiers: { tier: string; days: number | null; runs: number }[];
    };
    expect(preview.proposed).toBe(true);
    expect(preview.tiers.find((t) => t.tier === "trace")).toMatchObject({ days: 30, runs: 210 });
    expect((await get(["preview"], "trace_days=oops")).status).toBe(422);
    const reports = (await (await get(["reports"])).json()) as { items: { mode: string }[] };
    expect(reports.items.length).toBeGreaterThan(0);
  });
});
