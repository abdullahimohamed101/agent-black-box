import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readThrough } from "@/server/upstream";

const STREAM = ["v1", "runs", "run_01ABC", "stream"];

beforeEach(() => {
  vi.stubEnv("ABB_WEB_API_KEY", "abb_live_test.secret");
  vi.stubEnv("ABB_API_INTERNAL_URL", "http://api.internal");
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

const sse = (body: BodyInit | null, headers: Record<string, string> = {}) =>
  new Response(body, { status: 200, headers: { "content-type": "text/event-stream", ...headers } });

describe("stream proxy", () => {
  it("allows exactly the stream path of one run", async () => {
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "fixtures");
    for (const p of [
      ["v1", "runs", "stream"],
      ["v1", "runs", "r", "stream", "x"],
      ["v1", "stream"],
      ["v1", "runs", "a.b", "stream"],
    ]) {
      expect((await readThrough(p, new URLSearchParams())).status).toBe(404);
    }
  });

  it("has no live source for fixtures: a 404 envelope the client treats as 'poll instead'", async () => {
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "fixtures");
    const r = await readThrough(STREAM, new URLSearchParams());
    expect(r.status).toBe(404);
    expect(((await r.json()) as { error: { code: string } }).error.code).toBe(
      "STREAM_NOT_AVAILABLE",
    );
  });

  it("relays chunks as they arrive, without waiting for the upstream to finish", async () => {
    let push!: (chunk: string) => void;
    const upstream = new ReadableStream<Uint8Array>({
      start(controller) {
        const enc = new TextEncoder();
        push = (chunk) => controller.enqueue(enc.encode(chunk));
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sse(upstream, { "x-request-id": "req_1" })));
    const r = await readThrough(STREAM, new URLSearchParams());
    expect(r.status).toBe(200);
    expect(r.headers.get("content-type")).toBe("text/event-stream");
    expect(r.headers.get("cache-control")).toBe("no-store");
    expect(r.headers.get("x-accel-buffering")).toBe("no");
    expect(r.headers.get("x-content-type-options")).toBe("nosniff");
    expect(r.headers.get("x-request-id")).toBe("req_1");
    const reader = r.body!.getReader();
    push("id: evt_1\nevent: trace_event\ndata: {}\n\n"); // the upstream is still open
    const first = await reader.read();
    expect(new TextDecoder().decode(first.value)).toContain("id: evt_1");
    await reader.cancel();
  });

  it("sends the key and Last-Event-ID upstream, never the key downstream", async () => {
    const fetchMock = vi.fn().mockResolvedValue(sse("", {}));
    vi.stubGlobal("fetch", fetchMock);
    const r = await readThrough(STREAM, new URLSearchParams("last_event_id=evt_9"), {
      lastEventId: "evt_5",
    });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("http://api.internal/v1/runs/run_01ABC/stream?last_event_id=evt_9");
    expect(init.headers.authorization).toBe("Bearer abb_live_test.secret");
    expect(init.headers["last-event-id"]).toBe("evt_5");
    expect(init.headers.accept).toBe("text/event-stream");
    expect(JSON.stringify([...r.headers])).not.toContain("secret");
  });

  it("drops a malformed Last-Event-ID instead of forwarding it", async () => {
    const fetchMock = vi.fn().mockResolvedValue(sse(""));
    vi.stubGlobal("fetch", fetchMock);
    await readThrough(STREAM, new URLSearchParams(), { lastEventId: "evt_1\r\nx-evil: 1" });
    expect(fetchMock.mock.calls[0]![1].headers["last-event-id"]).toBeUndefined();
  });

  it("maps upstream auth failure to 502 and relays API errors (429, 404) unchanged", async () => {
    const json = (status: number, code: string, extra: Record<string, string> = {}) =>
      new Response(JSON.stringify({ error: { code } }), {
        status,
        headers: { "content-type": "application/json", ...extra },
      });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json(401, "API_KEY_INVALID")));
    const unauth = await readThrough(STREAM, new URLSearchParams());
    expect(unauth.status).toBe(502);
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(
          json(429, "STREAM_LIMIT", { "retry-after": "5", "x-request-id": "r" }),
        ),
    );
    const limited = await readThrough(STREAM, new URLSearchParams());
    expect(limited.status).toBe(429);
    expect(limited.headers.get("retry-after")).toBe("5");
    expect(((await limited.json()) as { error: { code: string } }).error.code).toBe("STREAM_LIMIT");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json(404, "RUN_NOT_FOUND")));
    expect((await readThrough(STREAM, new URLSearchParams())).status).toBe(404);
  });

  it("closes the upstream stream when the browser goes away", async () => {
    const seen: AbortSignal[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((_url: string, init: RequestInit) => {
        seen.push(init.signal!);
        return Promise.resolve(sse(new ReadableStream()));
      }),
    );
    const browser = new AbortController();
    await readThrough(STREAM, new URLSearchParams(), { signal: browser.signal });
    expect(seen[0]!.aborted).toBe(false);
    browser.abort();
    expect(seen[0]!.aborted).toBe(true);
  });

  it("gives up if the API cannot be reached, and does not time out a connected stream", async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
      const down = await readThrough(STREAM, new URLSearchParams());
      expect(down.status).toBe(503);
      let signal!: AbortSignal;
      vi.stubGlobal(
        "fetch",
        vi.fn().mockImplementation((_u: string, init: RequestInit) => {
          signal = init.signal!;
          return Promise.resolve(sse(new ReadableStream()));
        }),
      );
      await readThrough(STREAM, new URLSearchParams());
      await vi.advanceTimersByTimeAsync(60_000); // far past the 10 s connect timeout
      expect(signal.aborted).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("aborts a connect that hangs", async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockImplementation(
            (_u: string, init: RequestInit) =>
              new Promise((_resolve, reject) =>
                init.signal!.addEventListener("abort", () => reject(new Error("aborted"))),
              ),
          ),
      );
      const pending = readThrough(STREAM, new URLSearchParams());
      await vi.advanceTimersByTimeAsync(10_500);
      expect((await pending).status).toBe(503);
    } finally {
      vi.useRealTimers();
    }
  });
});
