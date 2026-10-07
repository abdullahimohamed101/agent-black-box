import { describe, expect, it } from "vitest";
import { fixtureRuns } from "@/fixtures";
import { computeDashboard } from "@/lib/dashboard";
import { formatCost, formatDuration, formatOffset, formatRelative } from "@/lib/format";

describe("computeDashboard", () => {
  const runs = fixtureRuns().map((f) => f.run);
  it("computes rates, cost, active agents from the sample", () => {
    const d = computeDashboard(runs);
    expect(d.sampleSize).toBe(runs.length);
    expect(d.finished).toBe(
      runs.filter((r) => !["RUNNING", "WAITING_FOR_APPROVAL"].includes(r.status)).length,
    );
    expect(d.successRate).toBeCloseTo(d.succeeded / d.finished);
    expect(d.failures).toBe(1);
    expect(d.activeRuns).toBe(2);
    expect(d.activeAgents).toEqual(["deploy-agent", "triage-agent"]);
    expect(d.totalCostUsd).toBeGreaterThan(10);
    expect(d.avgDurationMs).toBeGreaterThan(0);
  });
  it("is null-safe on an empty project", () => {
    const d = computeDashboard([]);
    expect(d).toMatchObject({
      successRate: null,
      avgDurationMs: null,
      failures: 0,
      totalCostUsd: 0,
    });
  });
});

describe("format", () => {
  it("durations", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(850)).toBe("850 ms");
    expect(formatDuration(1900)).toBe("1.9 s");
    expect(formatDuration(125_000)).toBe("2m 05s");
  });
  it("cost", () => {
    expect(formatCost(0.0121)).toBe("$0.0121");
    expect(formatCost(18.456)).toBe("$18.46");
    expect(formatCost(null)).toBe("—");
  });
  it("offsets and relative", () => {
    expect(formatOffset(1234)).toBe("+1.2s");
    expect(formatOffset(-5)).toBe("+0s");
    expect(formatRelative("2026-10-07T10:00:00Z", Date.parse("2026-10-07T10:05:00Z"))).toBe(
      "5m ago",
    );
  });
});
