import { describe, expect, it } from "vitest";
import { fixtureRuns } from "@/fixtures";
import { baselineCodingRun, wrongFixCodingRun } from "@/fixtures/codingRuns";
import { blockedRun, failureRetryRun } from "@/fixtures/runs";
import { withholdEvent } from "@/fixtures/withhold";
import type { EventOut } from "@/lib/api/types";
import { costDisplay, filesDisplay, projectAt, ReplayProjection } from "@/lib/replay";
import { headlineModel, replayHeadline } from "@/lib/runHeadline";

// Phase 12 review fixes. P1-1: a viewer's "files changed" must not collapse every withheld path into one file.
// P1-2: replay's cost is the server's settled per-call cost (ADR-121), never the caller's estimate alone, and it never
// claims an exactness it does not have. P3-4: a blocked run says "Blocked", not "First error".

const ctxOf = (f: { run: { status: string; first_error?: { event_id: string } | null } }) => ({
  status: f.run.status,
  firstErrorId: f.run.first_error?.event_id ?? null,
});
const t0Of = (events: readonly EventOut[]) =>
  Math.min(...events.map((e) => Date.parse(e.occurred_at)));
const last = <T>(a: readonly T[]) => a.length - 1;

describe("P1-1: files changed for a viewer (paths withheld)", () => {
  const wrong = wrongFixCodingRun(); // edits app/session.py then app/oauth.py: two files
  const viewerEvents = wrong.events.map((e) => withholdEvent(e, false));
  const filesModified = wrong.run.summary["files_modified"] as number;

  it("the fixture really touches two distinct files, and the owner's replay says two", () => {
    expect(filesModified).toBe(2);
    expect(projectAt(wrong.events, last(wrong.events), ctxOf(wrong)).counts.filesChanged).toBe(2);
  });

  it("withheld paths are counted one event at a time and the figure is approximate", () => {
    const s = projectAt(viewerEvents, last(viewerEvents), ctxOf(wrong));
    // before the fix every withheld path was the same key, so the viewer saw 1 file for 2 file events
    expect(s.counts.filesChanged).toBe(2);
    expect(s.filesApproximate).toBe(true);
  });

  it("three modified events on two paths: owner 2, viewer an upper bound of 3, flagged approximate", () => {
    const [a, b] = wrong.events.filter((e) => e.event_type === "file.modified");
    const fileEvents = [a!, b!, { ...a!, event_id: "evt_probe" } as EventOut];
    const owner = projectAt(fileEvents, 2, { status: "RUNNING" });
    const viewer = projectAt(
      fileEvents.map((e) => withholdEvent(e, false)),
      2,
      { status: "RUNNING" },
    );
    expect(owner.counts.filesChanged).toBe(2);
    expect(owner.filesApproximate).toBe(false);
    expect(viewer.counts.filesChanged).toBe(3);
    expect(viewer.filesApproximate).toBe(true);
    expect(filesDisplay(viewer)).toMatchObject({ text: "up to 3", approximate: true });
    expect(filesDisplay(owner)).toMatchObject({ text: "2", approximate: false });
  });

  it("at the last event of a finished run the viewer's figure is the run summary's exact count", () => {
    const s = projectAt(viewerEvents, last(viewerEvents), { ...ctxOf(wrong), filesModified });
    expect(s.counts.filesChanged).toBe(filesModified);
    expect(s.filesApproximate).toBe(false);
    // but not before the end, where only the per-event count is known
    const mid = projectAt(viewerEvents, last(viewerEvents) - 2, { ...ctxOf(wrong), filesModified });
    expect(mid.filesApproximate).toBe(true);
  });

  it("the baseline run edits one file twice: owner 1, viewer at most 2 until the end, then exactly 1", () => {
    const base = baselineCodingRun();
    const v = base.events.map((e) => withholdEvent(e, false));
    const n = last(v);
    expect(projectAt(base.events, n, ctxOf(base)).counts.filesChanged).toBe(1);
    expect(projectAt(v, n, ctxOf(base)).counts.filesChanged).toBe(2);
    expect(projectAt(v, n, { ...ctxOf(base), filesModified: 1 }).counts.filesChanged).toBe(1);
  });

  it("an owner's figure is never approximate and ignores the summary override", () => {
    const s = projectAt(wrong.events, last(wrong.events), { ...ctxOf(wrong), filesModified: 99 });
    expect(s.counts.filesChanged).toBe(2);
    expect(s.filesApproximate).toBe(false);
  });
});

describe("P1-2: cost so far is the server's settled cost", () => {
  const failed = failureRetryRun();
  const n = failed.events.length - 1;
  const ctx = ctxOf(failed);
  const sansAttributes = (e: EventOut): EventOut => {
    const { ["cost.estimated_usd"]: _drop, ...rest } = e.attributes;
    void _drop;
    return { ...e, attributes: rest };
  };

  it("a run priced by the server (no cost attribute at all) replays the summary's figure", () => {
    const events = failed.events.map(sansAttributes); // cost_usd stays: the API sends it whatever the caller did
    const s = projectAt(events, n, ctx);
    expect(s.costUsd).toBe(failed.run.summary["estimated_cost_usd"]);
    expect(s.costUsd).toBeGreaterThan(0);
    expect(s.unpricedCalls).toBe(0);
    expect(costDisplay(s).approximate).toBe(false);
    expect(costDisplay(s).floor).toBe(false);
  });

  it("every fixture run: the figure at the last event is the summary's, with and without the attribute", () => {
    for (const f of fixtureRuns()) {
      const c = ctxOf(f);
      const top = f.events.length - 1;
      const withAttrs = projectAt(f.events, top, c);
      const without = projectAt(f.events.map(sansAttributes), top, c);
      expect(withAttrs.costUsd, String(f.run.name)).toBe(f.run.summary["estimated_cost_usd"]);
      expect(without.costUsd, String(f.run.name)).toBe(withAttrs.costUsd);
      expect(without.unpricedCalls, String(f.run.name)).toBe(withAttrs.unpricedCalls);
    }
  });

  it("a call the server could not price is counted unpriced and makes the figure a floor", () => {
    const events = failed.events.map((e) =>
      e.event_type === "llm.request.completed" ? { ...e, cost_usd: 0, cost_unpriced: true } : e,
    );
    const calls = events.filter((e) => e.event_type === "llm.request.completed").length;
    const s = projectAt(events, n, ctx);
    expect(s.costUsd).toBe(0);
    expect(s.unpricedCalls).toBe(calls);
    expect(costDisplay(s)).toMatchObject({
      text: "at least $0.00",
      note: `${calls} calls unpriced`,
      floor: true,
    });
  });

  it("before the server has priced a call (a live arrival) the engine's own order applies: provider, then caller", () => {
    const call = failed.events.find((e) => e.event_type === "llm.request.completed")!;
    const raw = (attrs: Record<string, unknown>): EventOut => {
      const { cost_usd: _a, cost_unpriced: _b, ...rest } = call;
      void _a;
      void _b;
      return { ...rest, attributes: { ...call.attributes, ...attrs }, cost_unpriced: false };
    };
    const both = projectAt([raw({ "cost.provider_usd": 2, "cost.estimated_usd": 3 })], 0, ctx);
    expect(both.costUsd).toBe(2); // provider-reported wins (ADR-040)
    expect(both.unpricedCalls).toBe(0);
    expect(costDisplay(both)).toMatchObject({ text: "about $2.00", approximate: true });
    const clientOnly = projectAt([raw({ "cost.provider_usd": undefined })], 0, ctx);
    expect(clientOnly.costUsd).toBe(call.attributes["cost.estimated_usd"]);
    const neither = projectAt(
      [raw({ "cost.provider_usd": undefined, "cost.estimated_usd": undefined })],
      0,
      ctx,
    );
    expect(neither.costUsd).toBe(0);
    expect(neither.unpricedCalls).toBe(1);
    expect(costDisplay(neither)).toMatchObject({ text: "at least $0.00", note: "1 call unpriced" });
  });

  it("only completed model calls carry cost (the engine prices nothing else)", () => {
    const tool = failed.events.find((e) => e.event_type.startsWith("tool."))!;
    const s = projectAt(
      [{ ...tool, attributes: { ...tool.attributes, "cost.estimated_usd": 5 } }],
      0,
      ctx,
    );
    expect(s.costUsd).toBe(0);
    expect(s.unpricedCalls).toBe(0);
  });

  it("the replay headline says 'at least' and names the unpriced calls when the figure is a floor", () => {
    const events = failed.events.map((e) =>
      e.event_type === "llm.request.completed" ? { ...e, cost_usd: 0, cost_unpriced: true } : e,
    );
    const run = failed.run;
    const live = headlineModel({ run, events, t0: t0Of(events), eventsComplete: true });
    const s = projectAt(events, n, ctx);
    const text = replayHeadline(live, s).detail;
    expect(text).toContain("at least $0.00 so far");
    expect(text).toMatch(/\d+ calls unpriced/);
  });
});

describe("P3-4: a blocked run says Blocked", () => {
  const blocked = blockedRun();
  const at = blocked.events.findIndex((e) => e.event_type === "policy.action.blocked");

  it("the status line says Blocked from the blocking event on, not Running until the run ends", () => {
    const ctx = ctxOf(blocked);
    expect(at).toBeGreaterThan(0);
    expect(projectAt(blocked.events, at - 1, ctx).statusText).toBe("Running");
    expect(projectAt(blocked.events, at, ctx).statusText).toBe("Blocked");
    expect(projectAt(blocked.events, at + 1, ctx).statusText).toBe("Blocked");
  });

  it("a blocking event in a run that is not blocked in the end does not turn the status to Blocked", () => {
    const s = projectAt(blocked.events, at, { status: "SUCCESS" });
    expect(s.statusText).toBe("Running");
  });

  it("the headline says 'Blocked at step N', not 'First error', for a non-error blocking event", () => {
    const ctx = ctxOf(blocked);
    const live = headlineModel({
      run: blocked.run,
      events: blocked.events,
      t0: t0Of(blocked.events),
      eventsComplete: true,
    });
    const reached = replayHeadline(live, projectAt(blocked.events, at, ctx));
    expect(reached.lead).toMatch(/^Blocked at step \d+/);
    expect(reached.lead).not.toContain("First error");
    // a genuine error still reads as one
    const f = failureRetryRun();
    const liveF = headlineModel({
      run: f.run,
      events: f.events,
      t0: t0Of(f.events),
      eventsComplete: true,
    });
    const errAt = f.events.findIndex((e) => e.event_id === f.run.first_error!.event_id);
    expect(replayHeadline(liveF, projectAt(f.events, errAt, ctxOf(f))).lead).toMatch(
      /^First error at step/,
    );
  });
});

describe("P3-7: the run's length is computed once per event list", () => {
  it("durationMs does not rescan the timestamps on each read", () => {
    const f = failureRetryRun();
    const p = new ReplayProjection(f.events, ctxOf(f));
    const first = p.durationMs;
    // poison the parsed times behind the projection's back: a rescanning getter would notice, a cached value would not
    (p as unknown as { times: Float64Array }).times = new Float64Array(f.events.length).fill(0);
    expect(p.durationMs).toBe(first);
  });
});
