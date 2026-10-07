import { afterEach, describe, expect, it, vi } from "vitest";
import { readThrough } from "@/server/upstream";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("read proxy", () => {
  it("rejects paths outside the read allowlist", async () => {
    for (const p of [
      ["v1", "events"],
      ["v1", "runs", "x", "delete"],
      ["admin"],
      ["x", "v1", "runs"],
      ["v1", "runs", "x", "events", "y", "z"],
      ["v1", "runs", "a.b"],
      ["v1", "runs", "..", "keys"],
    ]) {
      expect((await readThrough(p, new URLSearchParams())).status).toBe(404);
    }
  });

  it("serves fixtures when configured", async () => {
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "fixtures");
    const r = await readThrough(["v1", "runs"], new URLSearchParams("limit=1"));
    expect(r.status).toBe(200);
    expect(((await r.json()) as { items: unknown[] }).items).toHaveLength(1);
  });

  it("refuses fixtures in production unless explicitly allowed", async () => {
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "fixtures");
    vi.stubEnv("NODE_ENV", "production");
    const r = await readThrough(["v1", "runs"], new URLSearchParams());
    expect(r.status).toBe(503);
    expect(((await r.json()) as { error: { code: string } }).error.code).toBe("FIXTURES_DISABLED");
    vi.stubEnv("ABB_WEB_ALLOW_FIXTURES", "1");
    expect((await readThrough(["v1", "runs"], new URLSearchParams())).status).toBe(200);
  });

  it("caps the query string and marks responses no-store/nosniff", async () => {
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "fixtures");
    expect(
      (await readThrough(["v1", "runs"], new URLSearchParams({ q: "x".repeat(3000) }))).status,
    ).toBe(414);
    const ok = await readThrough(["v1", "runs"], new URLSearchParams());
    expect(ok.headers.get("cache-control")).toBe("no-store");
    expect(ok.headers.get("x-content-type-options")).toBe("nosniff");
  });

  it("fails closed without a key and never exposes it to the caller", async () => {
    vi.stubEnv("ABB_WEB_API_KEY", "");
    expect((await readThrough(["v1", "runs"], new URLSearchParams())).status).toBe(503);
  });

  it("forwards the key server-side only, relays request id, maps upstream auth failure to 502", async () => {
    vi.stubEnv("ABB_WEB_API_KEY", "abb_live_test.secret");
    vi.stubEnv("ABB_API_INTERNAL_URL", "http://api.internal");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('{"items":[],"next_cursor":null}', {
        status: 200,
        headers: { "x-request-id": "req_1" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const ok = await readThrough(["v1", "runs"], new URLSearchParams("limit=2"));
    expect(fetchMock.mock.calls[0]![0]).toBe("http://api.internal/v1/runs?limit=2");
    expect((fetchMock.mock.calls[0]![1] as RequestInit).headers).toMatchObject({
      authorization: "Bearer abb_live_test.secret",
    });
    expect(ok.headers.get("x-request-id")).toBe("req_1");
    expect(JSON.stringify([...ok.headers])).not.toContain("secret");

    fetchMock.mockResolvedValue(new Response("{}", { status: 401 }));
    expect((await readThrough(["v1", "runs"], new URLSearchParams())).status).toBe(502);

    fetchMock.mockRejectedValue(new Error("down"));
    const down = await readThrough(["v1", "runs"], new URLSearchParams());
    expect(down.status).toBe(503);
    expect(((await down.json()) as { error: { retryable: boolean } }).error.retryable).toBe(true);
  });
});
