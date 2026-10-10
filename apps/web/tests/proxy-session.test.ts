import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readThrough } from "@/server/upstream";

const ORIGIN = "http://localhost:3000";
const SESSION = "s".repeat(43);
const WS = "ws_00000000000000000000000001";

beforeEach(() => {
  vi.stubEnv("ABB_WEB_ORIGIN", ORIGIN);
  vi.stubEnv("ABB_API_INTERNAL_URL", "http://api.internal");
  vi.stubEnv("ABB_WEB_DATA_SOURCE", "api");
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

function stubFetch(res: Response = Response.json({ items: [] })) {
  const fetchMock = vi.fn(async () => res);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
const sent = (fetchMock: ReturnType<typeof vi.fn>) => {
  const [url, init] = fetchMock.mock.calls[0]! as [string, RequestInit];
  return { url, init, headers: init.headers as Record<string, string> };
};
const body = (text: string) =>
  new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(new TextEncoder().encode(text));
      c.close();
    },
  });
const withSession = (extra: Record<string, string> = {}) =>
  new Headers({ cookie: `abb_session=${SESSION}`, ...extra });

describe("proxy header allowlist (D17)", () => {
  it("forwards exactly the session cookie, workspace, Accept and Last-Event-ID", async () => {
    const f = stubFetch();
    await readThrough(["v1", "runs", "run_1", "events"], new URLSearchParams("limit=5"), {
      headers: withSession({
        authorization: "Bearer stolen",
        "x-forwarded-for": "6.6.6.6",
        "x-forwarded-host": "evil.example",
        forwarded: "for=6.6.6.6",
        "x-request-id": "spoofed",
        "x-abb-workspace": WS,
        "last-event-id": "evt_9",
        "user-agent": "browser",
        referer: "http://localhost:3000/x",
        cookie: `other=1; abb_session=${SESSION}; analytics=2`,
      }),
    });
    const { url, headers } = sent(f);
    expect(url).toBe("http://api.internal/v1/runs/run_1/events?limit=5");
    expect(headers).toEqual({
      accept: "application/json",
      cookie: `abb_session=${SESSION}`,
      "x-abb-workspace": WS,
      "last-event-id": "evt_9",
    });
  });

  it("ignores a malformed session cookie and a malformed workspace header", async () => {
    const f = stubFetch();
    const res = await readThrough(["v1", "runs"], new URLSearchParams(), {
      headers: new Headers({ cookie: "abb_session=short" }),
    });
    expect(res.status).toBe(401);
    expect(f).not.toHaveBeenCalled();
    await readThrough(["v1", "runs"], new URLSearchParams(), {
      headers: withSession({ "x-abb-workspace": "ws_1; x-evil=1 /../" }),
    });
    expect(sent(f).headers["x-abb-workspace"]).toBeUndefined();
  });

  it("turns the stream's ?workspace= into the header and keeps it out of the upstream query", async () => {
    const f = stubFetch(
      new Response("event: x\n\n", { headers: { "content-type": "text/event-stream" } }),
    );
    await readThrough(
      ["v1", "runs", "run_1", "stream"],
      new URLSearchParams(`workspace=${WS}&last_event_id=evt_1`),
      { headers: withSession() },
    );
    const { url, headers } = sent(f);
    expect(url).toBe("http://api.internal/v1/runs/run_1/stream?last_event_id=evt_1");
    expect(headers["x-abb-workspace"]).toBe(WS);
    expect(headers.accept).toBe("text/event-stream");
  });

  it("uses __Host-abb_session under https and ignores the plain name there", async () => {
    vi.stubEnv("ABB_WEB_ORIGIN", "https://abb.example");
    const f = stubFetch();
    expect(
      (
        await readThrough(["v1", "me"], new URLSearchParams(), {
          headers: new Headers({ cookie: `abb_session=${SESSION}` }),
        })
      ).status,
    ).toBe(401);
    await readThrough(["v1", "me"], new URLSearchParams(), {
      headers: new Headers({ cookie: `__Host-abb_session=${SESSION}` }),
    });
    expect(sent(f).headers.cookie).toBe(`__Host-abb_session=${SESSION}`);
  });
});

describe("proxy responses", () => {
  it("never relays Set-Cookie and marks everything no-store", async () => {
    stubFetch(
      new Response("{}", {
        headers: { "set-cookie": "abb_session=evil; Path=/", "x-request-id": "req_9" },
      }),
    );
    const res = await readThrough(["v1", "me"], new URLSearchParams(), { headers: withSession() });
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(res.headers.get("x-request-id")).toBe("req_9");
  });

  it("relays 401 and 403 from a session as they are (the page signs in or shows the refusal)", async () => {
    for (const status of [401, 403, 404, 409, 422]) {
      stubFetch(Response.json({ error: { code: "X" } }, { status }));
      const res = await readThrough(["v1", "members"], new URLSearchParams(), {
        headers: withSession(),
      });
      expect(res.status).toBe(status);
    }
  });

  it("does not follow an upstream redirect", async () => {
    const f = stubFetch(
      new Response(null, { status: 302, headers: { location: "https://evil/" } }),
    );
    const res = await readThrough(["v1", "me"], new URLSearchParams(), { headers: withSession() });
    expect(res.status).toBe(502);
    expect(res.headers.get("location")).toBeNull();
    expect(sent(f).init.redirect).toBe("manual");
  });

  it("passes 204 as an empty response", async () => {
    stubFetch(new Response(null, { status: 204 }));
    const res = await readThrough(["v1", "api-keys", "key_1"], new URLSearchParams(), {
      method: "DELETE",
      headers: withSession({ origin: ORIGIN }),
    });
    expect(res.status).toBe(204);
    expect(await res.text()).toBe("");
  });
});

describe("proxy writes: exact Origin, JSON, bounded", () => {
  const write = (
    extra: Record<string, string> = {},
    text = '{"email":"a@b.co","role":"VIEWER"}',
  ) => ({
    method: "POST",
    headers: withSession({ origin: ORIGIN, "content-type": "application/json", ...extra }),
    body: body(text),
  });

  it("forwards the JSON body, content type and the configured origin", async () => {
    const f = stubFetch(Response.json({}, { status: 201 }));
    const res = await readThrough(["v1", "invitations"], new URLSearchParams(), write());
    expect(res.status).toBe(201);
    const { init, headers } = sent(f);
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"email":"a@b.co","role":"VIEWER"}');
    expect(headers).toMatchObject({ origin: ORIGIN, "content-type": "application/json" });
  });

  it("rejects a missing, null, foreign or look-alike Origin before any upstream call", async () => {
    const f = stubFetch();
    for (const origin of [
      undefined,
      "null",
      "https://evil.example",
      "http://localhost:3001",
      "https://localhost:3000",
    ]) {
      const base = write();
      const headers = new Headers(base.headers);
      if (origin) headers.set("origin", origin);
      else headers.delete("origin");
      const res = await readThrough(["v1", "invitations"], new URLSearchParams(), {
        ...base,
        headers,
      });
      expect(res.status, String(origin)).toBe(403);
      expect(((await res.json()) as { error: { code: string } }).error.code).toBe("CSRF_REJECTED");
    }
    expect(f).not.toHaveBeenCalled();
  });

  it("applies the origin rule to PATCH and DELETE too", async () => {
    const f = stubFetch();
    for (const method of ["PATCH", "DELETE"]) {
      const res = await readThrough(["v1", "members", "usr_1"], new URLSearchParams(), {
        method,
        headers: withSession({ "content-type": "application/json" }),
        body: body("{}"),
      });
      expect(res.status, method).toBe(403);
    }
    expect(f).not.toHaveBeenCalled();
  });

  it("requires ABB_WEB_ORIGIN for writes", async () => {
    vi.stubEnv("ABB_WEB_ORIGIN", "");
    expect((await readThrough(["v1", "invitations"], new URLSearchParams(), write())).status).toBe(
      503,
    );
  });

  it("refuses non-JSON bodies (415) and bodies over 64 KiB (413)", async () => {
    const f = stubFetch();
    expect(
      (
        await readThrough(
          ["v1", "invitations"],
          new URLSearchParams(),
          write({ "content-type": "text/plain" }),
        )
      ).status,
    ).toBe(415);
    const big = "x".repeat(70 * 1024);
    expect(
      (await readThrough(["v1", "invitations"], new URLSearchParams(), write({}, big))).status,
    ).toBe(413);
    expect(
      (
        await readThrough(
          ["v1", "invitations"],
          new URLSearchParams(),
          write({ "content-length": String(70 * 1024) }),
        )
      ).status,
    ).toBe(413);
    expect(f).not.toHaveBeenCalled();
  });

  it("needs a session for writes: no cookie is 401", async () => {
    const f = stubFetch();
    const res = await readThrough(["v1", "invitations"], new URLSearchParams(), {
      method: "POST",
      headers: new Headers({ origin: ORIGIN, "content-type": "application/json" }),
      body: body("{}"),
    });
    expect(res.status).toBe(401);
    expect(f).not.toHaveBeenCalled();
  });
});

describe("proxy routes", () => {
  it("allows the settings paths with the right methods only", async () => {
    stubFetch();
    const origin = withSession({ origin: ORIGIN, "content-type": "application/json" });
    const cases: [string, string[], number][] = [
      ["GET", ["v1", "me"], 200],
      ["GET", ["v1", "projects"], 200],
      ["GET", ["v1", "audit"], 200],
      ["POST", ["v1", "pricing", "overrides"], 200],
      ["POST", ["v1", "cost", "rebuild"], 200],
      ["DELETE", ["v1", "api-keys", "key_1"], 200],
      ["POST", ["v1", "me"], 405],
      ["DELETE", ["v1", "members"], 405],
      ["PUT", ["v1", "members", "usr_1"], 405],
      ["PATCH", ["v1", "runs", "run_1"], 405],
      ["GET", ["v1", "auth", "login"], 404],
      ["POST", ["v1", "auth", "logout"], 405],
      ["POST", ["v1", "events"], 405],
      ["GET", ["v1", "members", "a/b"], 404],
    ];
    for (const [method, path, status] of cases) {
      const res = await readThrough(path, new URLSearchParams(), {
        method,
        headers: origin,
        body: method === "POST" || method === "PATCH" ? body("{}") : null,
      });
      expect(res.status, `${method} ${path.join("/")}`).toBe(status);
    }
  });
});

describe("no shared credential", () => {
  it("a read without a session cookie is 401 and nothing is sent upstream", async () => {
    const f = stubFetch();
    const res = await readThrough(["v1", "runs"], new URLSearchParams(), {
      headers: new Headers(),
    });
    expect(res.status).toBe(401);
    expect(((await res.json()) as { error: { code: string } }).error.code).toBe("SESSION_INVALID");
    expect(f).not.toHaveBeenCalled();
  });

  it("never puts an Authorization header on the upstream request", async () => {
    const f = stubFetch();
    await readThrough(["v1", "runs"], new URLSearchParams(), {
      headers: withSession({ authorization: "Bearer abb_live_x.y" }),
    });
    expect(sent(f).headers.authorization).toBeUndefined();
    expect(sent(f).headers.cookie).toBe(`abb_session=${SESSION}`);
  });
});
