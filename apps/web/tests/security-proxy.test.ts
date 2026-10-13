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

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("security routes through the proxy (API mode)", () => {
  beforeEach(() => {
    vi.stubEnv("ABB_WEB_ORIGIN", ORIGIN);
    vi.stubEnv("ABB_API_INTERNAL_URL", "http://api.internal");
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "api");
  });

  it("forwards the two reads as GET and the settings read and write", async () => {
    const f = vi.fn(async () => Response.json({ items: [] }));
    vi.stubGlobal("fetch", f);
    for (const [method, path] of [
      ["GET", "v1/security/findings"],
      ["GET", "v1/security/detectors"],
      ["GET", "v1/projects/prj_1/security-settings"],
      ["PUT", "v1/projects/prj_1/security-settings"],
    ] as const) {
      const r = await readThrough(path.split("/"), new URLSearchParams("limit=5"), {
        method,
        headers: headers(),
        body: method === "PUT" ? body('{"capture_mode":"FULL"}') : null,
      });
      expect(r.status, `${method} ${path}`).toBe(200);
    }
    const put = f.mock.calls.at(-1) as unknown as [string, RequestInit];
    expect(put[1].method).toBe("PUT");
    expect(put[1].body).toBe('{"capture_mode":"FULL"}');
    expect((put[1].headers as Record<string, string>).origin).toBe(ORIGIN); // same exact-origin rule as other writes
  });

  it("refuses the wrong method, a cross-origin PUT and a PUT without a JSON body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({})),
    );
    const put = (h: Headers) =>
      readThrough(["v1", "projects", "prj_1", "security-settings"], new URLSearchParams(), {
        method: "PUT",
        headers: h,
        body: body("{}"),
      });
    expect((await put(headers({ origin: "http://evil.test" }))).status).toBe(403);
    expect((await put(headers({ "content-type": "text/plain" }))).status).toBe(415);
    for (const [method, path] of [
      ["POST", "v1/security/findings"],
      ["PUT", "v1/security/detectors"],
      ["DELETE", "v1/projects/prj_1/security-settings"],
      ["PATCH", "v1/projects/prj_1/security-settings"],
    ] as const) {
      const r = await readThrough(path.split("/"), new URLSearchParams(), {
        method,
        headers: headers(),
        body: body("{}"),
      });
      expect(r.status, `${method} ${path}`).toBe(405);
    }
  });
});

describe("security routes in fixtures mode", () => {
  beforeEach(() => vi.stubEnv("ABB_WEB_DATA_SOURCE", "fixtures"));

  it("serves the list, detectors and settings, and keeps PUT state in memory", async () => {
    const list = await readThrough(["v1", "security", "findings"], new URLSearchParams("limit=3"));
    expect(list.status).toBe(200);
    expect(((await list.json()) as { items: unknown[] }).items).toHaveLength(3);
    const prj = "prj_00000000000000000000000001";
    const put = await readThrough(
      ["v1", "projects", prj, "security-settings"],
      new URLSearchParams(),
      {
        method: "PUT",
        headers: new Headers({ "content-type": "application/json" }),
        body: body('{"capture_mode":"DISABLED"}'),
      },
    );
    expect(put.status).toBe(200);
    const get = await readThrough(
      ["v1", "projects", prj, "security-settings"],
      new URLSearchParams(),
    );
    expect(((await get.json()) as { capture_mode: string }).capture_mode).toBe("DISABLED");
    await readThrough(["v1", "projects", prj, "security-settings"], new URLSearchParams(), {
      method: "PUT",
      headers: new Headers({ "content-type": "application/json" }),
      body: body('{"capture_mode":"METADATA_ONLY"}'),
    });
  });

  it("the viewer workspace cannot read or write settings (403) but reads the same findings", async () => {
    const viewer = new Headers({
      "x-abb-workspace": "ws_00000000000000000000000002",
      "content-type": "application/json",
    });
    const prj = "prj_00000000000000000000000001";
    expect(
      (
        await readThrough(["v1", "projects", prj, "security-settings"], new URLSearchParams(), {
          headers: viewer,
        })
      ).status,
    ).toBe(403);
    const owner = await (
      await readThrough(["v1", "security", "findings"], new URLSearchParams())
    ).json();
    const seen = await (
      await readThrough(["v1", "security", "findings"], new URLSearchParams(), { headers: viewer })
    ).json();
    expect(seen).toEqual(owner);
  });
});
