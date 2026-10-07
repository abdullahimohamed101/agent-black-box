import { describe, expect, it } from "vitest";
import { ApiRequestError } from "@/lib/api/client";
import { shouldRetry } from "@/lib/queryClient";

describe("retry policy", () => {
  const e = (status: number) => new ApiRequestError("x", status, "X", null, status >= 500);
  it("never retries deterministic 4xx", () => {
    expect(shouldRetry(0, e(404))).toBe(false);
    expect(shouldRetry(0, e(400))).toBe(false);
  });
  it("retries 429, 5xx and network errors, at most twice", () => {
    expect(shouldRetry(0, e(429))).toBe(true);
    expect(shouldRetry(1, e(503))).toBe(true);
    expect(shouldRetry(2, e(503))).toBe(false);
    expect(shouldRetry(0, new TypeError("fetch failed"))).toBe(true);
  });
});
