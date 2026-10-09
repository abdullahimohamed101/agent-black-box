import { describe, expect, it } from "vitest";
import { formatCost, formatDuration, formatOffset, formatRelative } from "@/lib/format";

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
