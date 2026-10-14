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
const APR = "apr_00000000000000000000000001";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

// Phase 14: the proxy carries exactly the routes the Approvals and Policies screens call, by method and path.
const READS = [
  "v1/approvals",
  "v1/approvals/pending-count",
  `v1/approvals/${APR}`,
  "v1/policies/current",
  "v1/policies/versions",
  "v1/policies/versions/3",
  "v1/policies/diff",
];
const WRITES = [
  "v1/policies/validate",
  "v1/policies/publish",
  "v1/policies/dry-run",
  `v1/approvals/${APR}/decision`,
];

describe("policy and approval routes through the proxy (API mode)", () => {
  beforeEach(() => {
    vi.stubEnv("ABB_WEB_ORIGIN", ORIGIN);
    vi.stubEnv("ABB_API_INTERNAL_URL", "http://api.internal");
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "api");
  });

  it("forwards each read as GET and each write as POST, with the exact-origin and JSON rules", async () => {
    const f = vi.fn(async () => Response.json({ ok: true }));
    vi.stubGlobal("fetch", f);
    for (const path of READS) {
      const r = await readThrough(path.split("/"), new URLSearchParams("limit=5"), {
        method: "GET",
        headers: headers(),
      });
      expect(r.status, `GET ${path}`).toBe(200);
    }
    for (const path of WRITES) {
      const r = await readThrough(path.split("/"), new URLSearchParams(), {
        method: "POST",
        headers: headers(),
        body: body('{"document":"version: 1"}'),
      });
      expect(r.status, `POST ${path}`).toBe(200);
      const sent = f.mock.calls.at(-1) as unknown as [string, RequestInit];
      expect(sent[1].method).toBe("POST");
      expect((sent[1].headers as Record<string, string>).origin).toBe(ORIGIN);
    }
  });

  it("refuses a cross-origin write, a missing origin, a non-JSON body and an oversized body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({})),
    );
    for (const path of WRITES) {
      const post = (h: Headers, text = "{}") =>
        readThrough(path.split("/"), new URLSearchParams(), {
          method: "POST",
          headers: h,
          body: body(text),
        });
      expect((await post(headers({ origin: "http://evil.test" }))).status, path).toBe(403);
      const noOrigin = headers();
      noOrigin.delete("origin");
      expect((await post(noOrigin)).status, path).toBe(403);
      expect((await post(headers({ "content-type": "text/plain" }))).status, path).toBe(415);
      expect((await post(headers(), "x".repeat(70 * 1024))).status, path).toBe(413);
    }
  });

  it("carries no other path and no other method", async () => {
    const f = vi.fn(async () => Response.json({}));
    vi.stubGlobal("fetch", f);
    const attempts: [string, string][] = [
      // the agent-facing routes are not the browser's to call
      ["POST", "v1/policy/decide"],
      ["GET", `v1/approvals/${APR}/wait`],
      ["POST", `v1/approvals/${APR}/consume`],
      ["DELETE", `v1/approvals/${APR}`],
      ["GET", "v1/policies/fail-modes"],
      // wrong methods on carried paths
      ["POST", "v1/approvals"],
      ["PUT", "v1/approvals/pending-count"],
      ["PATCH", `v1/approvals/${APR}`],
      ["GET", `v1/approvals/${APR}/decision`],
      ["PUT", `v1/approvals/${APR}/decision`],
      ["DELETE", `v1/approvals/${APR}/decision`],
      ["GET", "v1/policies/publish"],
      ["GET", "v1/policies/validate"],
      ["GET", "v1/policies/dry-run"],
      ["PUT", "v1/policies/publish"],
      ["POST", "v1/policies/current"],
      ["DELETE", "v1/policies/versions/3"],
      ["POST", "v1/policies/diff"],
      // near misses
      ["GET", "v1/policies"],
      ["GET", "v1/policies/versions/abc"],
      ["GET", "v1/policies/versions/3/extra"],
      ["POST", "v1/policies/validate/extra"],
      ["POST", `v1/approvals/${APR}/decision/extra`],
      ["POST", "v1/approvals/decision"],
      ["GET", "v1/approvals/pending-count/x"],
      ["GET", "v1/approvals/../policies/current"],
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

describe("policy and approval routes in fixtures mode", () => {
  beforeEach(() => vi.stubEnv("ABB_WEB_DATA_SOURCE", "fixtures"));
  const json = { "content-type": "application/json" };

  it("serves the reads and the writes, and refuses the same extra paths", async () => {
    const list = await readThrough(["v1", "approvals"], new URLSearchParams("state=pending"));
    expect(list.status).toBe(200);
    const validate = await readThrough(["v1", "policies", "validate"], new URLSearchParams(), {
      method: "POST",
      headers: new Headers(json),
      body: body('{"document":"version: 1\\npolicies: []\\n"}'),
    });
    expect(validate.status).toBe(200);
    expect(((await validate.json()) as { valid: boolean }).valid).toBe(true);
    for (const [method, path] of [
      ["POST", "v1/policy/decide"],
      ["DELETE", `v1/approvals/${APR}`],
      ["GET", `v1/approvals/${APR}/wait`],
    ] as const) {
      const r = await readThrough(path.split("/"), new URLSearchParams(), {
        method,
        headers: new Headers(json),
        body: method === "GET" ? null : body("{}"),
      });
      expect([404, 405], `${method} ${path}`).toContain(r.status);
    }
  });

  it("applies the JSON and size rules to fixture writes too", async () => {
    const post = (h: Record<string, string>, text: string) =>
      readThrough(["v1", "policies", "validate"], new URLSearchParams(), {
        method: "POST",
        headers: new Headers(h),
        body: body(text),
      });
    expect((await post({ "content-type": "text/plain" }, "{}")).status).toBe(415);
    expect((await post(json, "x".repeat(70 * 1024))).status).toBe(413);
    expect((await post(json, "not json")).status).toBe(422);
  });

  it("the viewer workspace gets 403 on every approval and policy route", async () => {
    const viewer = new Headers({ ...json, "x-abb-workspace": "ws_00000000000000000000000002" });
    for (const path of READS) {
      const r = await readThrough(path.split("/"), new URLSearchParams(), { headers: viewer });
      expect(r.status, path).toBe(403);
    }
    for (const path of WRITES) {
      const r = await readThrough(path.split("/"), new URLSearchParams(), {
        method: "POST",
        headers: viewer,
        body: body("{}"),
      });
      expect(r.status, path).toBe(403);
    }
  });
});
