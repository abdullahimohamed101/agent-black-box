import { describe, expect, it } from "vitest";
import { expensiveRun, failureRetryRun, successRun } from "@/fixtures/runs";
import {
  NO_FILTERS,
  buildRows,
  describe as describeEvent,
  eventClass,
  filterEvents,
  firstError,
  isError,
  groupKeys,
} from "@/lib/timeline";

describe("eventClass", () => {
  it.each([
    ["run.started", "run"],
    ["agent.failed", "run"],
    ["llm.request.completed", "llm"],
    ["tool.call.failed", "tool"],
    ["git.commit", "file"],
    ["file.read", "file"],
    ["shell.command.completed", "shell"],
    ["retry.attempted", "reliability"],
    ["rate_limit.hit", "reliability"],
    ["policy.violation", "approval"],
    ["custom.thing", "other"],
  ])("%s -> %s", (type, cls) => expect(eventClass(type)).toBe(cls));
});

describe("firstError", () => {
  it("is the earliest failure in canonical order, not the last", () => {
    const { events } = failureRetryRun();
    const first = firstError(events)!;
    expect(first.event_type).toBe("tool.call.failed");
    const all = events.filter((e) => e.event_type === "tool.call.failed");
    expect(all.length).toBeGreaterThan(1);
    expect(first.event_id).toBe(all[0]!.event_id);
  });
  it("is undefined for a clean run", () => expect(firstError(successRun().events)).toBeUndefined());
});

describe("isError", () => {
  const base = successRun().events[0]!;
  it.each([
    [{ status: "error" }, true],
    [{ status: "timeout" }, true],
    [{ status: "success" }, false],
    [{ status: null }, false],
    [{ status: null, event_type: "llm.request.failed" }, true],
  ])("%j -> %s", (patch, expected) => {
    expect(isError({ ...base, ...patch })).toBe(expected);
  });
});

describe("filterEvents", () => {
  const { events } = failureRetryRun();
  it("returns everything with no filters, preserving order", () => {
    expect(filterEvents(events, NO_FILTERS)).toEqual(events);
  });
  it("filters by class and keeps relative order", () => {
    const out = filterEvents(events, {
      classes: new Set(["tool", "reliability"]),
      errorsOnly: false,
    });
    expect(out.every((e) => ["tool", "reliability"].includes(eventClass(e.event_type)))).toBe(true);
    expect(out.map((e) => e.sequence)).toEqual(
      [...out.map((e) => e.sequence!)].sort((a, b) => a - b),
    );
  });
  it("errors-only keeps only errors", () => {
    const out = filterEvents(events, { classes: null, errorsOnly: true });
    expect(out.length).toBeGreaterThan(0);
    expect(out.every((e) => e.status === "error")).toBe(true);
  });
  it("an empty class set shows nothing", () => {
    expect(filterEvents(events, { classes: new Set(), errorsOnly: false })).toEqual([]);
  });
});

describe("buildRows grouping", () => {
  const { events } = successRun();
  it("groups consecutive same-span events and never reorders", () => {
    const rows = buildRows(events, new Set());
    const flat = rows
      .filter((r) => r.kind === "event")
      .map((r) => (r.kind === "event" ? r.event : null));
    expect(flat.map((e) => e!.event_id)).toEqual(events.map((e) => e.event_id));
    const groups = rows.filter((r) => r.kind === "group");
    expect(groups.length).toBeGreaterThan(0);
    expect(groups.every((g) => g.kind === "group" && g.count >= 2)).toBe(true);
  });
  it("collapsing a group hides its members only", () => {
    const open = buildRows(events, new Set());
    const keys = groupKeys(open);
    const closed = buildRows(events, new Set(keys));
    expect(closed.length).toBe(
      open.length -
        keys.reduce((n, k) => n + (open.find((r) => r.key === k) as { count: number }).count, 0),
    );
    expect(
      closed.filter((r) => r.kind === "group").every((r) => r.kind === "group" && r.collapsed),
    ).toBe(true);
  });
  it("marks a group containing an error", () => {
    const g = buildRows(failureRetryRun().events, new Set()).filter((r) => r.kind === "group");
    expect(g.some((r) => r.kind === "group" && r.hasError && r.status === "error")).toBe(true);
  });
});

describe("describe", () => {
  it("summarises llm cost/tokens and retries as text", () => {
    const llm = successRun().events.find((e) => e.event_type === "llm.request.completed")!;
    expect(describeEvent(llm)).toContain("model-x");
    expect(describeEvent(llm)).toContain("1,840 in");
    const retry = failureRetryRun().events.find((e) => e.event_type === "retry.attempted")!;
    expect(describeEvent(retry)).toBe("Retry #1 after ConnectionTimeout");
  });
  it("handles hostile attribute types", () => {
    const e = {
      ...successRun().events[0]!,
      event_type: "tool.call.started",
      attributes: { "tool.name": { x: 1 } },
    };
    expect(describeEvent(e)).toBe("tool");
  });
  it("expensive fixture sums cost", () => {
    expect(Number(expensiveRun().run.summary.estimated_cost_usd)).toBeGreaterThan(10);
  });
});
