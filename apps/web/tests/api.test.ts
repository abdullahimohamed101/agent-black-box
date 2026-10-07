import { describe, expect, it } from "vitest";
import { fetchReadiness } from "@/lib/api";

const json = (body: unknown, status = 200) =>
  Promise.resolve(
    new Response(JSON.stringify(body), { status, headers: { "x-request-id": "req_h" } }),
  );

describe("fetchReadiness", () => {
  it("returns ok with the API version", async () => {
    const result = await fetchReadiness(
      () => json({ status: "ok", version: "1.2.3", database: "ok" }),
      "http://x",
    );
    expect(result).toEqual({ state: "ok", version: "1.2.3" });
  });

  it("maps the typed error envelope", async () => {
    const result = await fetchReadiness(
      () =>
        json(
          {
            error: {
              code: "DEPENDENCY_UNAVAILABLE",
              message: "Database is not reachable.",
              request_id: "req_1",
            },
          },
          503,
        ),
      "http://x",
    );
    expect(result).toEqual({
      state: "error",
      message: "Database is not reachable.",
      requestId: "req_1",
    });
  });

  it("never throws when the network fails", async () => {
    const result = await fetchReadiness(() => Promise.reject(new Error("boom")), "http://x");
    expect(result).toEqual({ state: "unreachable", message: "boom" });
  });
});
