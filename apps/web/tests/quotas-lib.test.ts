import { describe, expect, it } from "vitest";
import {
  barPercent,
  formatBytes,
  formatCount,
  inputValue,
  parseOverride,
  quotaMeta,
  quotaStatus,
  usageSentence,
  type QuotaRow,
} from "@/lib/quotas";

describe("formatting", () => {
  it.each([
    [0, "0 B"],
    [1023, "1023 B"],
    [1024, "1 KiB"],
    [1536, "1.50 KiB"],
    [5 * 1024 ** 3, "5 GiB"],
    [Math.round(1.25 * 1024 ** 3), "1.25 GiB"],
    [1024 ** 5, "1 PiB"],
  ])("formats %s bytes as %s", (n, text) => {
    expect(formatBytes(n)).toBe(text);
  });
  it("groups counts and survives nonsense", () => {
    expect(formatCount(5_000_000)).toBe("5,000,000");
    expect(formatCount(Number.NaN)).toBe("—");
    expect(formatBytes(-1)).toBe("—");
  });
  it("clamps the bar but not the number", () => {
    expect([barPercent(-5), barPercent(40), barPercent(200), barPercent(Number.NaN)]).toEqual([
      0, 40, 100, 0,
    ]);
  });
  it("says a quota it has never heard of plainly and does not throw", () => {
    expect(quotaMeta("mystery")).toBeNull();
    expect(quotaStatus("mystery").word).toBe("Within limit");
    const row = {
      quota: "agents",
      used: 12,
      limit: 500,
    } as QuotaRow;
    expect(usageSentence(row)).toBe("12 of 500 agents");
  });
});

describe("a typed override mirrors the API's bounds", () => {
  it("treats empty as the default", () => {
    expect(parseOverride("agents", "   ")).toEqual({ ok: true, value: null });
  });
  it.each([
    ["agents", "1", 1],
    ["agents", "1,000,000", 1_000_000],
    ["agents", " 500 ", 500],
    ["runs_per_day", "50_000", 50_000],
    ["events_per_day", "100000000000", 100_000_000_000],
    ["artifact_bytes", "5120", 5120 * 1024 ** 2],
  ] as const)("accepts %s %s", (q, text, value) => {
    expect(parseOverride(q, text)).toEqual({ ok: true, value });
  });
  it.each([
    ["agents", "0"],
    ["agents", "1000001"],
    ["agents", "-3"],
    ["agents", "1.5"],
    ["agents", "ten"],
    ["agents", "1e3"],
    ["runs_per_day", "100000001"],
    ["events_per_day", "100000000001"],
    ["artifact_bytes", "0"],
    ["artifact_bytes", String(1024 ** 3 + 1)],
    ["artifact_bytes", "9".repeat(30)],
  ] as const)("refuses %s %s with a message that does not echo it", (q, text) => {
    const r = parseOverride(q, text);
    expect(r.ok).toBe(false);
    if (!r.ok && text.trim().length > 2) expect(r.message).not.toContain(text);
  });
  it("shows an override in the unit it is typed in", () => {
    expect(inputValue("artifact_bytes", 5120 * 1024 ** 2)).toBe("5120");
    expect(inputValue("agents", 77)).toBe("77");
    expect(inputValue("agents", null)).toBe("");
  });
});
