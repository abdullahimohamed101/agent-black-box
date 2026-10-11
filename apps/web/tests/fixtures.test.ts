import { describe, expect, it } from "vitest";
import { fixtureReply } from "@/fixtures/api";
import { fixtureRuns } from "@/fixtures";
import { orderingMode, sortEvents } from "@/lib/ordering";
import { stressRun, successRun } from "@/fixtures/runs";
import type { EventPage, RunPage } from "@/lib/api/types";

const q = (s = "") => new URLSearchParams(s);

describe("fixtures", () => {
  it("are deterministic", () => {
    expect(JSON.stringify(successRun())).toBe(JSON.stringify(successRun()));
    expect(JSON.stringify(stressRun().events.slice(0, 50))).toBe(
      JSON.stringify(stressRun().events.slice(0, 50)),
    );
  });

  it("cover success, failure+retry, expensive, running, waiting and the 10,000-event stress run", () => {
    const runs = fixtureRuns().map((f) => f.run);
    expect(new Set(runs.map((r) => r.status))).toEqual(
      new Set(["SUCCESS", "FAILED", "RUNNING", "WAITING_FOR_APPROVAL"]),
    );
    const stress = fixtureRuns().find((f) => f.run.summary.event_count === 10_000);
    expect(stress?.events).toHaveLength(10_000);
    expect(runs.some((r) => Number(r.summary.estimated_cost_usd) > 10)).toBe(true);
    expect(runs.some((r) => Number(r.summary.retry_count) > 0 && r.status === "FAILED")).toBe(true);
  });

  it("the multi-agent run has five agents, a remote emitter without sequence, and is time-ordered", () => {
    const f = fixtureRuns().find((x) => x.run.name === "Ship feature: multi-agent")!;
    expect(new Set(f.events.map((e) => e.agent_id))).toEqual(
      new Set(["planner", "researcher", "coder", "tester", "reviewer"]),
    );
    expect(f.run.ordering_mode).toBe("time");
    expect(f.run.agent_id).toBe("planner");
    const remote = f.events.filter((e) => e.agent_id === "tester");
    expect(remote.length).toBeGreaterThan(0);
    expect(remote.every((e) => e.sequence == null)).toBe(true);
    expect(f.events.filter((e) => e.agent_id !== "tester").every((e) => e.sequence != null)).toBe(
      true,
    );
    expect(f.run.summary.agent_count).toBe(5);
    expect(f.events.filter((e) => e.event_type === "tool.call.failed")).toHaveLength(1);
  });

  it("the failure run closes its root span with a registered closer carrying the error", () => {
    const f = fixtureRuns().find((x) => x.run.status === "FAILED")!;
    expect(f.events.some((e) => e.event_type === "agent.failed")).toBe(false);
    const closer = f.events.find((e) => e.event_type === "agent.completed");
    expect(closer?.status).toBe("error");
  });

  it("emit unique, ordered, pattern-valid ids", () => {
    for (const f of fixtureRuns()) {
      const ids = new Set(f.events.map((e) => e.event_id));
      expect(ids.size).toBe(f.events.length);
      // canonical order, and the mode the run reports is the mode that order implies
      expect(f.events).toEqual(sortEvents(f.events));
      expect(f.run.ordering_mode).toBe(orderingMode(f.events));
      if (f.run.ordering_mode === "sequence")
        expect(f.events.every((e, i) => e.sequence === i + 1)).toBe(true);
      expect(f.run.id).toMatch(/^run_[0-7][0-9A-HJKMNP-TV-Z]{25}$/);
    }
  });
});

describe("fixture API", () => {
  it("lists newest first, filters and pages by cursor", () => {
    const all = fixtureReply(["v1", "runs"], q()).body as RunPage;
    expect(all.items.map((r) => r.started_at)).toEqual(
      [...all.items.map((r) => r.started_at)].sort().reverse(),
    );
    const p1 = fixtureReply(["v1", "runs"], q("limit=2")).body as RunPage;
    const p2 = fixtureReply(["v1", "runs"], q(`limit=2&cursor=${p1.next_cursor}`)).body as RunPage;
    expect([...p1.items, ...p2.items].map((r) => r.id)).toEqual(
      all.items.slice(0, 4).map((r) => r.id),
    );
    const failed = fixtureReply(["v1", "runs"], q("status=FAILED")).body as RunPage;
    expect(failed.items.every((r) => r.status === "FAILED")).toBe(true);
    expect(fixtureReply(["v1", "runs"], q("cursor=garbage")).status).toBe(400);
  });

  it("pages events in canonical order without payloads, detail has payload", () => {
    const run = successRun().run;
    const p = fixtureReply(["v1", "runs", run.id, "events"], q("limit=5")).body as EventPage;
    expect(p.items.map((e) => e.sequence)).toEqual([1, 2, 3, 4, 5]);
    expect(p.items.every((e) => e.payload === null)).toBe(true);
    const llm = successRun().events.find((e) => e.has_payload)!;
    const d = fixtureReply(["v1", "runs", run.id, "events", llm.event_id], q());
    expect((d.body as { payload: unknown }).payload).not.toBeNull();
    expect(fixtureReply(["v1", "runs", "run_nope"], q()).status).toBe(404);
  });
});
