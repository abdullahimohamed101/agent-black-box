import { describe, expect, it } from "vitest";
import { fixtureRuns } from "@/fixtures";
import { multiAgentRun, successRun } from "@/fixtures/runs";
import type { EventOut } from "@/lib/api/types";
import {
  MAX_DELAY_MS,
  MIN_DELAY_MS,
  ReplayPacer,
  clampGap,
  groupStops,
  replayClockSkewed,
  stepBack,
  stepForward,
} from "@/lib/replay";
import { buildRows } from "@/lib/timeline";

const ev = (type: string, at: string | null | undefined, extra: Partial<EventOut> = {}): EventOut =>
  ({
    ...successRun().events[0]!,
    event_id: `evt_${Math.random().toString(36).slice(2)}`,
    event_type: type,
    occurred_at: at as string,
    ...extra,
  }) as EventOut;
const T0 = Date.parse("2026-10-07T09:00:00Z");
const at = (ms: number) => new Date(T0 + ms).toISOString();

describe("replay pacing", () => {
  it("clamps a gap to 80 ms-1.5 s at 1x", () => {
    expect(clampGap(0)).toBe(MIN_DELAY_MS);
    expect(clampGap(10)).toBe(80);
    expect(clampGap(80)).toBe(80);
    expect(clampGap(700)).toBe(700);
    expect(clampGap(1500)).toBe(1500);
    expect(clampGap(40_000)).toBe(MAX_DELAY_MS);
  });

  it("2x and 5x divide the clamped delay", () => {
    expect(clampGap(700, 2)).toBe(350);
    expect(clampGap(700, 5)).toBe(140);
    expect(clampGap(40_000, 5)).toBe(300);
    expect(clampGap(1, 5)).toBe(16);
    expect(clampGap(700, 0)).toBe(700); // a nonsense speed is 1x
    expect(clampGap(700, NaN)).toBe(700);
  });

  it("delayBefore uses the recorded gap between consecutive events", () => {
    const p = new ReplayPacer([
      ev("run.started", at(0)),
      ev("llm.request.started", at(500)),
      ev("llm.request.completed", at(500 + 40_000)),
      ev("tool.call.started", at(500 + 40_010)),
    ]);
    expect(p.delayBefore(0)).toBe(0); // nothing precedes the first event
    expect(p.delayBefore(1)).toBe(500);
    expect(p.delayBefore(1, 2)).toBe(250);
    expect(p.delayBefore(2)).toBe(1500);
    expect(p.delayBefore(2, 5)).toBe(300);
    expect(p.delayBefore(3)).toBe(80);
    expect(p.delayBefore(3, 5)).toBe(16);
    expect(p.delayBefore(99)).toBe(0);
  });

  it("a negative gap (clock skew) is zero, so it plays at the minimum", () => {
    const p = new ReplayPacer([ev("a.b", at(5000)), ev("a.c", at(1000)), ev("a.d", at(1200))]);
    expect(p.gapMs(1)).toBe(0);
    expect(p.delayBefore(1)).toBe(80);
    expect(p.delayBefore(2)).toBe(200);
    expect(p.gapNote(1)).toBeNull();
  });

  it("a missing, empty or unparseable timestamp is a zero gap on either side", () => {
    const p = new ReplayPacer([
      ev("a.b", at(0)),
      ev("a.c", null),
      ev("a.d", "not a time"),
      ev("a.e", at(900)),
      ev("a.f", ""),
    ]);
    for (const i of [1, 2, 3, 4]) {
      expect(p.gapMs(i)).toBe(0);
      expect(p.delayBefore(i)).toBe(80);
      expect(Number.isFinite(p.delayBefore(i, 5))).toBe(true);
      expect(p.gapNote(i)).toBeNull();
    }
  });

  it("labels a clamped gap in words and names the preceding event, never its content", () => {
    const events = [
      ev("llm.request.started", at(0), { attributes: { "llm.model": "secret-model-name" } }),
      ev("llm.request.completed", at(40_000)),
      ev("tool.call.started", at(100_000)),
      ev("tool.call.completed", at(100_500)),
      ev("shell.command.started", at(100_600)),
      ev("shell.command.completed", at(190_600)),
      ev("approval.requested", at(190_700)),
      ev("approval.granted", at(190_700 + 125_000)),
    ];
    const p = new ReplayPacer(events);
    expect(p.gapNote(1)).toBe("40 s model call, skipped to 1.5 s");
    expect(p.gapNote(2)).toBe("1m 00s gap after the model call, skipped to 1.5 s");
    expect(p.gapNote(3)).toBeNull(); // 500 ms: played as recorded
    expect(p.gapNote(5)).toBe("1m 30s shell command, skipped to 1.5 s");
    expect(p.gapNote(7)).toBe("2m 05s gap after the approval, skipped to 1.5 s");
    expect(p.gapNote(0)).toBeNull();
    expect(JSON.stringify(events.map((_, i) => p.gapNote(i)))).not.toContain("secret");
  });

  it("a gap of exactly 1.5 s is not clamped, so it has no note", () => {
    const p = new ReplayPacer([ev("a.b", at(0)), ev("a.c", at(1500)), ev("a.d", at(3001))]);
    expect(p.gapNote(1)).toBeNull();
    expect(p.gapNote(2)).not.toBeNull();
  });

  it("on every fixture run the delays are finite, in range and in canonical order", () => {
    for (const f of fixtureRuns()) {
      const p = new ReplayPacer(f.events);
      for (let i = 0; i < f.events.length; i += i < 200 ? 1 : 37) {
        const d = p.delayBefore(i, 1);
        if (i === 0) expect(d).toBe(0);
        else {
          expect(d).toBeGreaterThanOrEqual(MIN_DELAY_MS);
          expect(d).toBeLessThanOrEqual(MAX_DELAY_MS);
        }
      }
    }
  });

  it("a skewed multi-agent run (one emitter's clock behind) paces by clamped gaps only", () => {
    const f = multiAgentRun();
    // The tester's process clock runs ten minutes behind; canonical order is unchanged, so the clocks go backwards.
    const events = f.events.map((e) =>
      e.agent_id === "tester"
        ? { ...e, occurred_at: new Date(Date.parse(e.occurred_at) - 600_000).toISOString() }
        : e,
    );
    const times = events.map((e) => Date.parse(e.occurred_at));
    expect(times.some((t, i) => i > 0 && t < times[i - 1]!)).toBe(true);
    const p = new ReplayPacer(events);
    for (let i = 1; i < events.length; i++) {
      expect(p.gapMs(i)).toBeGreaterThanOrEqual(0);
      for (const speed of [1, 2, 5]) {
        expect(p.delayBefore(i, speed)).toBeGreaterThanOrEqual(MIN_DELAY_MS / speed);
        expect(p.delayBefore(i, speed)).toBeLessThanOrEqual(MAX_DELAY_MS / speed);
      }
    }
    // a backwards step into the skewed process reads as zero, not as a huge jump either way
    const back = times.findIndex((t, i) => i > 0 && t < times[i - 1]!);
    expect(p.gapMs(back)).toBe(0);
    expect(p.gapNote(back)).toBeNull();
    expect(replayClockSkewed(f.run, events)).toBe(true);
  });
});

describe("replay group stops", () => {
  it("a request's started and completed events are one stop; single-event mode is every event", () => {
    const f = successRun();
    const stops = groupStops(f.events);
    const single = groupStops(f.events, false);
    expect(single).toHaveLength(f.events.length);
    expect(single.every((s, i) => s.start === i && s.end === i)).toBe(true);
    expect(stops.length).toBeLessThan(f.events.length);
    const i = f.events.findIndex((e) => e.event_type === "llm.request.started");
    expect(stops.find((s) => s.start === i)).toEqual({ start: i, end: i + 1 });
  });

  it("stops tile the run exactly and match the timeline's own groups", () => {
    for (const f of fixtureRuns()) {
      const stops = groupStops(f.events);
      let next = 0;
      for (const s of stops) {
        expect(s.start).toBe(next);
        next = s.end + 1;
      }
      expect(next).toBe(f.events.length);
      const groups = buildRows(f.events, new Set())
        .filter((r) => r.kind === "group")
        .map((r) => (r.kind === "group" ? r.count : 0));
      expect(stops.filter((s) => s.end > s.start).map((s) => s.end - s.start + 1)).toEqual(groups);
    }
  });

  it("steps forward to the end of the next stop and back to the end of the previous one", () => {
    const stops = [
      { start: 0, end: 0 },
      { start: 1, end: 2 },
      { start: 3, end: 3 },
      { start: 4, end: 6 },
    ];
    expect(stepForward(stops, 0)).toBe(2);
    expect(stepForward(stops, 2)).toBe(3);
    expect(stepForward(stops, 1)).toBe(2); // mid-stop finishes the stop
    expect(stepForward(stops, 6)).toBe(6); // the end stays the end
    expect(stepBack(stops, 6)).toBe(3);
    expect(stepBack(stops, 3)).toBe(2);
    expect(stepBack(stops, 2)).toBe(0);
    expect(stepBack(stops, 5)).toBe(3); // mid-stop goes to the stop before
    expect(stepBack(stops, 0)).toBe(0);
    expect(stepForward([], 0)).toBe(0);
    expect(stepBack([], 0)).toBe(0);
  });

  it("walking a fixture by stops visits each stop end once, both ways", () => {
    const f = successRun();
    const stops = groupStops(f.events);
    const fwd = [0];
    for (let i = stepForward(stops, 0); fwd[fwd.length - 1] !== f.events.length - 1;) {
      fwd.push(i);
      i = stepForward(stops, i);
    }
    const ends = [...new Set(stops.map((s) => s.end))];
    expect(fwd).toEqual(ends);
    const back = [fwd[fwd.length - 1]!];
    while (back[back.length - 1]! > 0) back.push(stepBack(stops, back[back.length - 1]!));
    expect(back.reverse()).toEqual(ends);
  });
});

describe("replay clock skew", () => {
  it("flags the multi-agent run whose emitters' clocks disagree, and not a single-process run", () => {
    const m = multiAgentRun();
    expect(replayClockSkewed(m.run, m.events)).toBe(true);
    const s = successRun();
    expect(replayClockSkewed(s.run, s.events)).toBe(false);
  });
});
