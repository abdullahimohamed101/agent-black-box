import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readThrough } from "@/server/upstream";

const ORIGIN = "http://localhost:3000";
const SESSION = "s".repeat(43);

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
  const fetchMock = vi.fn(async () => res.clone());
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
const bodyOf = (text: string) =>
  new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(new TextEncoder().encode(text));
      c.close();
    },
  });
const session = (extra: Record<string, string> = {}) =>
  new Headers({ cookie: `abb_session=${SESSION}`, ...extra });
const json = { origin: ORIGIN, "content-type": "application/json" };
const call = (method: string, path: string, body = "{}", headers = session(json)) =>
  readThrough(path.split("/"), new URLSearchParams(), {
    method,
    headers,
    body: method === "POST" ? bodyOf(body) : null,
  });

describe("Phase 11 proxy routes", () => {
  it("allows each evaluation, comparison and experiment operation with its method only", async () => {
    stubFetch();
    const cases: [string, string, number][] = [
      ["GET", "v1/evaluators", 200],
      ["POST", "v1/evaluators", 200],
      ["DELETE", "v1/evaluators/evr_1", 200],
      ["GET", "v1/runs/run_1/evaluations", 200],
      ["POST", "v1/runs/run_1/evaluations", 200],
      ["POST", "v1/runs/run_1/verdicts", 200],
      ["GET", "v1/runs/run_1/compare", 200],
      ["GET", "v1/experiments", 200],
      ["POST", "v1/experiments", 200],
      ["GET", "v1/experiments/exp_1", 200],
      ["DELETE", "v1/experiments/exp_1", 200],
      ["POST", "v1/experiments/exp_1/variants", 200],
      ["POST", "v1/experiments/exp_1/runs", 200],
      ["DELETE", "v1/experiments/exp_1/runs/run_1", 200],
      ["POST", "v1/experiments/exp_1/evaluate", 200],
      // wrong methods
      ["GET", "v1/runs/run_1/verdicts", 405], // the verdict route is a write only
      ["POST", "v1/runs/run_1/compare", 405],
      ["DELETE", "v1/runs/run_1/evaluations", 405],
      ["PATCH", "v1/evaluators/evr_1", 405],
      ["PUT", "v1/experiments/exp_1", 405],
      ["GET", "v1/experiments/exp_1/evaluate", 405],
      ["DELETE", "v1/evaluators", 405],
      // not routes at all
      ["GET", "v1/evaluators/evr_1", 405], // there is no single-evaluator read: only the DELETE
      ["GET", "v1/experiments/exp_1/variants", 405],
      ["GET", "v1/experiments/exp_1/runs/run_1", 405],
      ["GET", "v1/runs/run_1/evaluations/eval_1", 404],
      ["GET", "v1/runs/run_1/compare/extra", 404],
      ["GET", "v1/evaluators/a.b", 404],
      ["GET", "v1/experiments/a%2Fb", 404],
    ];
    for (const [method, path, status] of cases) {
      const res = await call(method, path);
      expect(res.status, `${method} ${path}`).toBe(status);
    }
  });

  it("forwards compare's query string, the workspace header and the body unchanged", async () => {
    const f = stubFetch();
    await readThrough(
      ["v1", "runs", "run_1", "compare"],
      new URLSearchParams({ with_run_id: "run_2" }),
      { headers: session({ "x-abb-workspace": "ws_00000000000000000000000001" }) },
    );
    const [url, init] = f.mock.calls[0]! as unknown as [string, RequestInit];
    expect(url).toBe("http://api.internal/v1/runs/run_1/compare?with_run_id=run_2");
    expect((init.headers as Record<string, string>)["x-abb-workspace"]).toBe(
      "ws_00000000000000000000000001",
    );
    expect(init.redirect).toBe("manual");

    f.mockClear();
    const text = '{"evaluator_id":"evr_1","label":"pass","explanation":"<b>x</b>"}';
    await call("POST", "v1/runs/run_1/verdicts", text);
    const [, init2] = f.mock.calls[0]! as unknown as [string, RequestInit];
    expect(init2.body).toBe(text);
    expect((init2.headers as Record<string, string>).origin).toBe(ORIGIN);
  });

  it("every new write needs the exact Origin, a JSON body of at most 64 KiB and a session", async () => {
    const f = stubFetch();
    const writes: [string, string][] = [
      ["POST", "v1/evaluators"],
      ["DELETE", "v1/evaluators/evr_1"],
      ["POST", "v1/runs/run_1/evaluations"],
      ["POST", "v1/runs/run_1/verdicts"],
      ["POST", "v1/experiments"],
      ["DELETE", "v1/experiments/exp_1"],
      ["POST", "v1/experiments/exp_1/variants"],
      ["POST", "v1/experiments/exp_1/runs"],
      ["DELETE", "v1/experiments/exp_1/runs/run_1"],
      ["POST", "v1/experiments/exp_1/evaluate"],
    ];
    for (const [method, path] of writes) {
      for (const origin of [undefined, "null", "https://evil.example", "http://localhost:3001"]) {
        const headers = session({ "content-type": "application/json" });
        if (origin) headers.set("origin", origin);
        expect(
          (await call(method, path, "{}", headers)).status,
          `${method} ${path} ${origin}`,
        ).toBe(403);
      }
      expect(
        (
          await call(
            method,
            path,
            "{}",
            new Headers({ origin: ORIGIN, "content-type": "application/json" }),
          )
        ).status,
        `${method} ${path} without session`,
      ).toBe(401);
    }
    for (const [method, path] of writes.filter(([m]) => m === "POST")) {
      expect((await call(method, path, "x".repeat(70 * 1024))).status, path).toBe(413);
      expect(
        (await call(method, path, "{}", session({ origin: ORIGIN, "content-type": "text/plain" })))
          .status,
        path,
      ).toBe(415);
    }
    expect(f).not.toHaveBeenCalled();
  });

  it("never follows an upstream redirect from a new route", async () => {
    stubFetch(new Response(null, { status: 302, headers: { location: "https://evil.example" } }));
    const res = await call("POST", "v1/runs/run_1/verdicts");
    expect(res.status).toBe(502);
    expect(res.headers.get("location")).toBeNull();
  });

  it("relays the API's refusals for a verdict (viewer 403, archived 409, mismatch 422) as they are", async () => {
    for (const status of [403, 404, 409, 422]) {
      stubFetch(Response.json({ error: { code: "X", message: "m" } }, { status }));
      const res = await call("POST", "v1/runs/run_1/verdicts");
      expect(res.status).toBe(status);
    }
  });

  it("fixture mode is read-only for the new writes and serves the new reads", async () => {
    vi.stubEnv("ABB_WEB_DATA_SOURCE", "fixtures");
    expect((await call("POST", "v1/runs/run_1/verdicts")).status).toBe(405);
    const evaluators = await readThrough(["v1", "evaluators"], new URLSearchParams());
    expect(evaluators.status).toBe(200);
    expect(((await evaluators.json()) as { items: unknown[] }).items.length).toBeGreaterThan(0);
    const experiments = await readThrough(["v1", "experiments"], new URLSearchParams());
    expect(experiments.status).toBe(200);
  });
});
