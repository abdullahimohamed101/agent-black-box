import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readThrough } from "@/server/upstream";

const ORIGIN = "http://localhost:3000";
const SESSION = "s".repeat(43);
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
const DST = "dst_00000000000000000000000001";
const RUL = "rul_00000000000000000000000001";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

// Phase 17: the proxy carries exactly the routes the Alerts page calls, by method and path.
const CARRIED: [string, string][] = [
  ["GET", "v1/alerts/destinations"],
  ["GET", "v1/alerts/rules"],
  ["GET", "v1/alerts/notifications"],
  ["GET", `v1/alerts/rules/${RUL}/preview`],
  ["POST", "v1/alerts/destinations"],
  ["POST", "v1/alerts/rules"],
  ["POST", `v1/alerts/destinations/${DST}/test`],
  ["PATCH", `v1/alerts/destinations/${DST}`],
  ["PATCH", `v1/alerts/rules/${RUL}`],
  ["DELETE", `v1/alerts/destinations/${DST}`],
  ["DELETE", `v1/alerts/rules/${RUL}`],
];

describe("alert routes through the proxy (API mode)", () => {
  beforeEach(() => {
    vi.stubEnv("ABB_WEB_ORIGIN", ORIGIN);
    vi.stubEnv("ABB_API_INTERNAL_URL", "http://api.internal");
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "api");
  });

  it("forwards each carried route with its method, the exact origin and the Retry-After header back", async () => {
    const f = vi.fn(
      async () =>
        new Response("{}", {
          status: 429,
          headers: { "content-type": "application/json", "retry-after": "9" },
        }),
    );
    vi.stubGlobal("fetch", f);
    for (const [method, path] of CARRIED) {
      const r = await readThrough(path.split("/"), new URLSearchParams("limit=5"), {
        method,
        headers: headers(),
        body: method === "POST" || method === "PATCH" ? body("{}") : null,
      });
      expect(r.status, `${method} ${path}`).toBe(429);
      expect(r.headers.get("retry-after")).toBe("9");
      const sent = f.mock.calls.at(-1) as unknown as [string, RequestInit];
      expect(sent[1].method).toBe(method);
      if (method !== "GET") expect((sent[1].headers as Record<string, string>).origin).toBe(ORIGIN);
    }
  });

  it("refuses a cross-origin write, a missing origin, a non-JSON body and an oversized body", async () => {
    const f = vi.fn(async () => Response.json({}));
    vi.stubGlobal("fetch", f);
    for (const [method, path] of CARRIED.filter(([m]) => m === "POST" || m === "PATCH")) {
      const send = (h: Headers, text = "{}") =>
        readThrough(path.split("/"), new URLSearchParams(), {
          method,
          headers: h,
          body: body(text),
        });
      expect((await send(headers({ origin: "http://evil.test" }))).status, path).toBe(403);
      const none = headers();
      none.delete("origin");
      expect((await send(none)).status, path).toBe(403);
      expect((await send(headers({ "content-type": "text/plain" }))).status, path).toBe(415);
      expect((await send(headers(), "x".repeat(70 * 1024))).status, path).toBe(413);
    }
    for (const path of [`v1/alerts/destinations/${DST}`, `v1/alerts/rules/${RUL}`]) {
      const r = await readThrough(path.split("/"), new URLSearchParams(), {
        method: "DELETE",
        headers: headers({ origin: "http://evil.test" }),
      });
      expect(r.status, path).toBe(403);
    }
    expect(f).not.toHaveBeenCalled();
  });

  it("carries no other path and no other method", async () => {
    const f = vi.fn(async () => Response.json({}));
    vi.stubGlobal("fetch", f);
    const attempts: [string, string][] = [
      ["GET", `v1/alerts/destinations/${DST}`],
      ["GET", `v1/alerts/rules/${RUL}`],
      ["GET", `v1/alerts/destinations/${DST}/test`],
      ["PUT", "v1/alerts/rules"],
      ["PUT", `v1/alerts/rules/${RUL}`],
      ["PATCH", "v1/alerts/rules"],
      ["DELETE", "v1/alerts/rules"],
      ["DELETE", "v1/alerts/notifications"],
      ["POST", "v1/alerts/notifications"],
      ["POST", `v1/alerts/rules/${RUL}/preview`],
      ["POST", `v1/alerts/rules/${RUL}/test`],
      ["PATCH", `v1/alerts/destinations/${DST}/test`],
      ["GET", "v1/alerts"],
      ["GET", "v1/alerts/other"],
      ["GET", `v1/alerts/rules/${RUL}/preview/x`],
      ["GET", "v1/alerts/rules/../../me"],
      ["POST", `v1/alerts/destinations/${DST}/test/extra`],
    ];
    for (const [method, path] of attempts) {
      const r = await readThrough(path.split("/"), new URLSearchParams(), {
        method,
        headers: headers(),
        body: method === "GET" ? null : body("{}"),
      });
      expect([404, 405], `${method} ${path}`).toContain(r.status);
    }
    expect(f).not.toHaveBeenCalled();
  });
});

describe("alert routes in fixtures mode", () => {
  it("answers reads and writes from the in-memory fixtures, and 204 for a delete", async () => {
    vi.stubEnv("ABB_WEB_ORIGIN", ORIGIN);
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "fixtures");
    vi.stubEnv("ABB_WEB_ALLOW_FIXTURES", "1");
    const get = await readThrough(["v1", "alerts", "rules"], new URLSearchParams(), {});
    expect(get.status).toBe(200);
    const del = await readThrough(["v1", "alerts", "rules", RUL], new URLSearchParams(), {
      method: "DELETE",
      headers: headers(),
    });
    expect(del.status).toBe(204);
    const after = await readThrough(["v1", "alerts", "rules"], new URLSearchParams(), {});
    expect(((await after.json()) as { items: unknown[] }).items).toHaveLength(1);
    const noJson = await readThrough(["v1", "alerts", "rules"], new URLSearchParams(), {
      method: "POST",
      headers: headers({ "content-type": "text/plain" }),
      body: body("x"),
    });
    expect(noJson.status).toBe(415);
  });
});
