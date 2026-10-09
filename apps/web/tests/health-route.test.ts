import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "@/app/api/health/route";
import { fetchReadiness } from "@/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("/api/health (KI-065)", () => {
  it("relays the API's readiness from the server side", async () => {
    vi.stubEnv("ABB_API_INTERNAL_URL", "http://api.internal:9");
    const seen: string[] = [];
    vi.stubGlobal("fetch", (url: string) => {
      seen.push(url);
      return Promise.resolve(
        new Response(JSON.stringify({ status: "ok", version: "1.2.3", database: "ok" }), {
          headers: { "x-request-id": "req_1" },
        }),
      );
    });
    const res = await GET();
    expect(seen).toEqual(["http://api.internal:9/readyz"]);
    expect(res.status).toBe(200);
    expect(res.headers.get("x-request-id")).toBe("req_1");
    expect((await res.json()).version).toBe("1.2.3");
  });

  it("reports an unreachable API as a 502 error envelope", async () => {
    vi.stubGlobal("fetch", () => Promise.reject(new Error("ECONNREFUSED")));
    const res = await GET();
    expect(res.status).toBe(502);
    expect((await res.json()).error.message).toMatch(/cannot reach the API/);
  });

  it("the browser helper calls this origin, not the API", async () => {
    const seen: string[] = [];
    await fetchReadiness((url) => {
      seen.push(String(url));
      return Promise.resolve(
        new Response(JSON.stringify({ status: "ok", version: "0", database: "ok" })),
      );
    });
    expect(seen).toEqual(["/api/health"]);
  });
});
