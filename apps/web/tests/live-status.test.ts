import { describe, expect, it } from "vitest";
import type { EventOut } from "@/lib/api/types";
import { newestArrival } from "@/lib/live";
import { liveStatus } from "@/lib/liveStatus";

const ev = (
  event_type: string,
  attributes: Record<string, unknown> = {},
  over: Partial<EventOut> = {},
): EventOut =>
  ({
    event_id: `evt_${event_type}`,
    event_type,
    attributes,
    received_at: "2026-10-07T12:00:00Z",
    status: null,
    ...over,
  }) as EventOut;

describe("liveStatus", () => {
  it("is null with no events", () => expect(liveStatus([])).toBeNull());

  it.each([
    [ev("llm.request.started", { "llm.model": "m-1" }), "Calling model m-1", "work"],
    [ev("llm.request.started"), "Calling model", "work"],
    [ev("tool.call.started", { "tool.name": "grep" }), "Running tool grep", "work"],
    [ev("shell.command.started"), "Running a command", "work"],
    [ev("file.read", { "file.path": "src/auth.py" }), "Reading src/auth.py", "work"],
    [ev("file.modified", { "file.path": "src/auth.py" }), "Editing src/auth.py", "work"],
    [ev("retry.attempted"), "Retrying", "work"],
    [ev("approval.requested"), "Waiting for approval", "wait"],
    [ev("tool.call.completed"), "Planning", "work"],
    [ev("llm.request.completed"), "Planning", "work"],
    [ev("approval.granted"), "Planning", "work"],
    [ev("run.completed", {}, { status: "success" }), "Done", "done"],
    [ev("run.completed", {}, { status: "error" }), "Failed", "bad"],
    [ev("run.failed"), "Failed", "bad"],
    [ev("run.cancelled"), "Cancelled", "bad"],
    [ev("something.unknown"), "Working", "work"],
  ])("%#: describes the newest event", (event, label, tone) => {
    expect(liveStatus([ev("run.started"), event])).toEqual({ label, tone });
  });

  it("uses only the newest event and shortens long paths from the left", () => {
    const long = `${"d/".repeat(40)}file.py`;
    const s = liveStatus([ev("run.completed"), ev("file.read", { "file.path": long })]);
    expect(s?.label.startsWith("Reading …")).toBe(true);
    expect(s?.label.endsWith("file.py")).toBe(true);
    expect(s!.label.length).toBeLessThan(60);
  });

  it("ignores a non-string or empty attribute", () => {
    expect(liveStatus([ev("tool.call.started", { "tool.name": 5 })])?.label).toBe("Running tool");
    expect(liveStatus([ev("tool.call.started", { "tool.name": "" })])?.label).toBe("Running tool");
  });
});

describe("newestArrival", () => {
  it("picks the event received last, comparing real times not strings", () => {
    const a = ev("a", {}, { event_id: "evt_a", received_at: "2026-10-07T12:00:00.500000Z" });
    const b = ev("b", {}, { event_id: "evt_b", received_at: "2026-10-07T12:00:00Z" }); // earlier
    const c = ev("c", {}, { event_id: "evt_c", received_at: "2026-10-07T12:00:01Z" });
    expect(newestArrival([a, b])).toBe("evt_a"); // "…:00Z" > "…:00.5Z" as plain strings
    expect(newestArrival([a, b, c])).toBe("evt_c");
    expect(newestArrival([])).toBeNull();
  });
});
