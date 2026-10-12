import { describe, expect, it } from "vitest";
import { fixtureRuns } from "@/fixtures";
import { multiAgentRun, stressRun, successRun } from "@/fixtures/runs";
import type { FixtureRun } from "@/fixtures/builder";
import type { EventOut } from "@/lib/api/types";
import { ReplayProjection, projectAt, replayOrder, type ReplayState } from "@/lib/replay";

// The parity property (Phase 12 acceptance 1): the state at any playhead equals an independent from-scratch computation
// over the events up to it. The naive version below shares no code with the projection: it filters, counts and sums.

const ERR = (e: EventOut) =>
  e.status === "error" || e.status === "timeout" || e.event_type.endsWith(".failed");

function naive(f: FixtureRun, k: number) {
  const so = f.events.slice(0, k + 1);
  const priced = so.map((e) => e.attributes["cost.estimated_usd"]).filter((c) => c != null);
  const completed = so.filter((e) => e.event_type === "llm.request.completed");
  const opens = new Map<string, number>();
  const closed: string[] = [];
  so.forEach((e, i) => {
    if (!e.span_id) return;
    if (e.event_type.endsWith(".started") && !opens.has(e.span_id) && !closed.includes(e.span_id))
      opens.set(e.span_id, i);
    else if (/\.(completed|failed)$/.test(e.event_type) && opens.has(e.span_id)) {
      opens.delete(e.span_id);
      closed.push(e.span_id);
    }
  });
  // the run's stored first error (the fixture builder derives it, blocked runs included), not the projection's logic
  const firstId = f.run.first_error?.event_id;
  return {
    events: so.length,
    errors: so.filter(ERR).length,
    retries: so.filter((e) => e.event_type === "retry.attempted").length,
    modelCalls: so.filter((e) => /^llm\.request\.(completed|failed)$/.test(e.event_type)).length,
    toolCalls: so.filter((e) => /^tool\.call\.(completed|failed)$/.test(e.event_type)).length,
    filesChanged: new Set(
      so
        .filter((e) => /^file\.(created|modified|deleted)$/.test(e.event_type))
        .map((e) => String(e.attributes["file.path"])),
    ).size,
    cost: Number(priced.reduce((s: number, c) => s + Number(c), 0).toFixed(6)),
    unpriced: completed.filter((e) => e.attributes["cost.estimated_usd"] == null).length,
    inTok: completed.reduce((s, e) => s + Number(e.attributes["llm.input_tokens"] ?? 0), 0),
    outTok: completed.reduce((s, e) => s + Number(e.attributes["llm.output_tokens"] ?? 0), 0),
    reached: firstId ? so.some((e) => e.event_id === firstId) : false,
    open: [...opens.keys()].sort(),
    finished: closed,
    agent: so[k]!.agent_id,
  };
}

function observed(s: ReplayState) {
  return {
    events: s.counts.events,
    errors: s.counts.errors,
    retries: s.counts.retries,
    modelCalls: s.counts.modelCalls,
    toolCalls: s.counts.toolCalls,
    filesChanged: s.counts.filesChanged,
    cost: s.costUsd,
    unpriced: s.unpricedCalls,
    inTok: s.inputTokens,
    outTok: s.outputTokens,
    reached: s.firstErrorReached,
    open: s.openSpans.map((x) => x.spanId).sort(),
    finished: s.finishedSpans.map((x) => x.spanId),
    agent: s.currentAgent,
  };
}

/** Deterministic pseudo-random walk so a failure reproduces. */
function lcg(seed: number) {
  let x = seed;
  return () => (x = (x * 1664525 + 1013904223) % 4294967296) / 4294967296;
}

const ctxOf = (f: FixtureRun) => ({
  status: f.run.status,
  firstErrorId: f.run.first_error?.event_id ?? null,
});

describe("replay projection: parity with a from-scratch computation", () => {
  for (const f of fixtureRuns()) {
    const n = f.events.length;
    it(`${f.run.name} (${n} events): any playhead, forward and backward`, () => {
      const rand = lcg(n + 7);
      const ks = [0, n - 1, Math.floor(n / 2), 1, n - 2].filter((k) => k >= 0 && k < n);
      const walks = Math.min(n > 1000 ? 40 : 80, n * 2);
      for (let i = 0; i < walks; i++) ks.push(Math.floor(rand() * n));
      // small checkpoint interval so backward jumps really restore snapshots on the short runs too
      const proj = new ReplayProjection(f.events, ctxOf(f), { checkpointEvery: 8 });
      for (const k of ks) {
        const state = proj.advance(k);
        expect(state.index).toBe(k);
        expect(state.current).toBe(f.events[k]);
        expect(observed(state)).toEqual(naive(f, k));
      }
      // a fresh projection agrees at a few points too (no hidden dependence on the walk)
      for (const k of [0, Math.floor(n / 3), n - 1])
        expect(observed(projectAt(f.events, k, ctxOf(f)))).toEqual(naive(f, k));
    });

    it(`${f.run.name}: at the last event the state is the run summary`, () => {
      const s = projectAt(f.events, n - 1, ctxOf(f));
      const sum = f.run.summary;
      expect(s.counts.events).toBe(sum["event_count"]);
      expect(s.costUsd).toBe(sum["estimated_cost_usd"]);
      expect(s.counts.retries).toBe(sum["retry_count"]);
      expect(s.counts.errors).toBe(sum["error_count"]);
      expect(s.counts.filesChanged).toBe(sum["files_modified"]);
      expect(s.counts.modelCalls).toBe(sum["llm_calls"]);
      expect(s.counts.toolCalls).toBe(sum["tool_calls"]);
      expect(s.inputTokens).toBe(sum["input_tokens"]);
      expect(s.outputTokens).toBe(sum["output_tokens"]);
      const first = f.run.first_error?.event_id;
      expect(s.firstErrorIndex).toBe(first ? f.events.findIndex((e) => e.event_id === first) : -1);
      expect(s.firstErrorReached).toBe(first != null);
      expect(s.filesApproximate).toBe(false);
    });
  }

  it("a blocked run without an error event reaches its blocking event (ADR-112)", () => {
    const f = fixtureRuns().find((x) => x.run.status === "BLOCKED")!;
    // no stored id: found through firstErrorFor, as the headline does
    const s0 = projectAt(f.events, 0, { status: "BLOCKED" });
    const last = projectAt(f.events, f.events.length - 1, { status: "BLOCKED" });
    expect(s0.firstErrorIndex).toBeGreaterThan(0);
    expect(s0.firstErrorReached).toBe(false);
    expect(last.firstErrorReached).toBe(true);
    expect(f.events[last.firstErrorIndex]!.event_id).toBe(f.run.first_error!.event_id);
    expect(last.counts.errors).toBe(0);
  });

  it("the first error is reached exactly at its own event", () => {
    const f = fixtureRuns().find((x) => x.run.status === "FAILED")!;
    const at = f.events.findIndex((e) => e.event_id === f.run.first_error!.event_id);
    const p = new ReplayProjection(f.events, ctxOf(f));
    expect(p.advance(at - 1).firstErrorReached).toBe(false);
    expect(p.advance(at).firstErrorReached).toBe(true);
    expect(p.advance(at - 1).firstErrorReached).toBe(false);
  });

  it("describes the run as of the moment, and the open spans for the waterfall", () => {
    const f = successRun();
    const s0 = projectAt(f.events, 0, ctxOf(f));
    expect(s0.statusText).toBe("Running");
    expect(projectAt(f.events, f.events.length - 1, ctxOf(f)).statusText).toBe("Completed");
    const start = f.events.findIndex((e) => e.event_type === "llm.request.started");
    const mid = projectAt(f.events, start, ctxOf(f));
    expect(
      mid.openSpans.some((x) => x.spanId === f.events[start]!.span_id && x.kind === "llm"),
    ).toBe(true);
    const done = projectAt(f.events, start + 1, ctxOf(f));
    expect(done.openSpans.some((x) => x.spanId === f.events[start]!.span_id)).toBe(false);
    const fin = done.finishedSpans.find((x) => x.spanId === f.events[start]!.span_id)!;
    expect([fin.startIndex, fin.endIndex]).toEqual([start, start + 1]);
  });

  it("tracks the current agent in a multi-agent run", () => {
    const f = multiAgentRun();
    const agents = new ReplayProjection(f.events, ctxOf(f));
    const seen = new Set<string | null>();
    for (let k = 0; k < f.events.length; k++) seen.add(agents.advance(k).currentAgent);
    expect(seen).toEqual(new Set(["planner", "researcher", "coder", "tester", "reviewer"]));
  });

  it("an empty run projects to nothing and does not throw", () => {
    const s = projectAt([], 0, { status: "RUNNING" });
    expect(s).toMatchObject({ index: -1, total: 0, current: null, firstErrorReached: false });
    expect(s.counts.events).toBe(0);
  });

  it("clamps a playhead outside the run", () => {
    const f = successRun();
    expect(projectAt(f.events, 9999, ctxOf(f)).index).toBe(f.events.length - 1);
    expect(projectAt(f.events, -5, ctxOf(f)).index).toBe(0);
    expect(projectAt(f.events, NaN, ctxOf(f)).index).toBe(0);
  });

  it("keeps its state when a live run's events only grow, and starts over when they change underneath", () => {
    const f = successRun();
    const part = f.events.slice(0, 10);
    const p = new ReplayProjection(part, { status: "RUNNING" }, { checkpointEvery: 4 });
    p.advance(9);
    expect(p.setEvents(f.events, { status: "RUNNING" })).toBe(true);
    expect((p.advance(10), p.lastApplied)).toBe(1); // only the new event
    expect(observed(p.advance(f.events.length - 1))).toEqual(naive(f, f.events.length - 1));
    const without = f.events.filter((_, i) => i !== 3);
    expect(p.setEvents(without, { status: "RUNNING" })).toBe(false);
    const g = { ...f, events: without };
    expect(observed(p.advance(without.length - 1))).toEqual(naive(g, without.length - 1));
  });
});

describe("replay projection: incremental and checkpointed", () => {
  it("a forward move applies only the new events", () => {
    const f = stressRun();
    const p = new ReplayProjection(f.events, ctxOf(f));
    expect((p.advance(100), p.lastApplied)).toBe(101);
    expect((p.advance(101), p.lastApplied)).toBe(1);
    expect((p.advance(110), p.lastApplied)).toBe(9);
    expect((p.advance(110), p.lastApplied)).toBe(0);
  });

  it("a backward jump restores the nearest checkpoint and re-applies less than one interval", () => {
    const f = stressRun();
    const every = 64;
    const p = new ReplayProjection(f.events, ctxOf(f), { checkpointEvery: every });
    p.advance(f.events.length - 1);
    for (const k of [9000, 4097, 4096, 4095, 300, 63, 64, 0, 5000]) {
      const s = p.advance(k);
      expect(p.lastApplied, `jump to ${k}`).toBeLessThanOrEqual(every);
      expect(observed(s)).toEqual(naive(f, k));
    }
  });

  it("checkpoints never leak into each other: restoring twice gives the same state", () => {
    const f = multiAgentRun();
    const p = new ReplayProjection(f.events, ctxOf(f), { checkpointEvery: 5 });
    p.advance(f.events.length - 1);
    const a = observed(p.advance(12));
    p.advance(f.events.length - 1);
    p.advance(3);
    expect(observed(p.advance(12))).toEqual(a);
  });
});

describe("replay projection: canonical order, never the clock", () => {
  it("follows the order it is given when the clocks run backwards", () => {
    const f = successRun();
    const n = f.events.length;
    const skewed = f.events.map((e, i) => ({
      ...e,
      occurred_at: new Date(Date.parse("2026-10-07T10:00:00Z") + (n - i) * 1000).toISOString(),
    }));
    const p = new ReplayProjection(skewed, ctxOf(f));
    for (let k = 0; k < n; k++) {
      const s = p.advance(k);
      expect(s.current!.event_id).toBe(f.events[k]!.event_id);
      expect(s.counts.events).toBe(k + 1);
    }
    // elapsed time never goes backwards, though every clock does
    let last = 0;
    for (let k = 0; k < n; k++) {
      const el = p.advance(k).elapsedMs;
      expect(el).toBeGreaterThanOrEqual(last);
      last = el;
    }
  });

  it("replayOrder restores the canonical order from arrival order", () => {
    const f = multiAgentRun();
    const shuffled = [...f.events].reverse();
    expect(replayOrder(shuffled).map((e) => e.event_id)).toEqual(f.events.map((e) => e.event_id));
  });
});

describe("replay projection: performance (acceptance 8)", () => {
  it("jumps anywhere in the 10,000-event run well under 100 ms (median, generous bound)", () => {
    const f = stressRun();
    expect(f.events).toHaveLength(10_000);
    const p = new ReplayProjection(f.events, ctxOf(f));
    const rand = lcg(3);
    const times: number[] = [];
    for (let i = 0; i < 40; i++) {
      const to = i % 2 === 0 ? f.events.length - 1 : 0; // start <-> end
      const t = performance.now();
      p.advance(to);
      times.push(performance.now() - t);
    }
    for (let i = 0; i < 40; i++) {
      const t = performance.now();
      p.advance(Math.floor(rand() * f.events.length));
      times.push(performance.now() - t);
    }
    times.sort((a, b) => a - b);
    expect(times[Math.floor(times.length / 2)]!).toBeLessThan(100);
    expect(times[times.length - 1]!).toBeLessThan(1000); // even the first cold walk to the end
  });
});
