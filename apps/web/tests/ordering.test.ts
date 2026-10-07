import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { mergeEvents, micros, orderingMode, sortEvents, type Orderable } from "@/lib/ordering";

type Golden = { cases: { mode: string; events: Orderable[]; expected: string[] }[] };
const golden: Golden = JSON.parse(
  readFileSync(
    path.resolve(__dirname, "../../../packages/event-schema/tests/data/ordering-golden.json"),
    "utf8",
  ),
);

const ev = (
  id: string,
  sequence: number | null,
  occurred: string,
  received: string | null = null,
): Orderable => ({
  event_id: id,
  sequence,
  occurred_at: occurred,
  received_at: received,
});

describe("parity with the Python implementation", () => {
  it("has golden cases", () => expect(golden.cases.length).toBeGreaterThanOrEqual(50));

  it.each(golden.cases.map((c, i) => [i, c] as const))(
    "case %i sorts exactly like sort_events",
    (_i, c) => {
      expect(orderingMode(c.events)).toBe(c.mode);
      expect(sortEvents(c.events).map((e) => e.event_id)).toEqual(c.expected);
    },
  );

  it.each(golden.cases.map((c, i) => [i, c] as const))(
    "case %i merges to the same order in any arrival split",
    (_i, c) => {
      // deliver the shuffled events in three chunks, newest-first, each possibly repeated
      const third = Math.ceil(c.events.length / 3);
      const chunks = [
        c.events.slice(2 * third),
        c.events.slice(third, 2 * third),
        c.events.slice(0, third),
      ];
      let state = mergeEvents<Orderable>([], []);
      for (const chunk of chunks) state = mergeEvents(state.events, [...chunk, ...chunk]);
      expect(state.events.map((e) => e.event_id)).toEqual(c.expected);
    },
  );
});

describe("micros", () => {
  it("keeps sub-millisecond differences that Date would lose", () => {
    expect(micros("2026-10-07T12:00:00.000002Z")).toBe(micros("2026-10-07T12:00:00.000001Z") + 1n);
    expect(micros("2026-10-07T12:00:00Z")).toBe(micros("2026-10-07T12:00:00.000000Z"));
  });
  it("reads short fractions as the right number of microseconds", () => {
    const whole = micros("2026-10-07T12:00:00Z");
    expect(micros("2026-10-07T12:00:00.5Z")).toBe(whole + 500_000n);
    expect(micros("2026-10-07T12:00:00.123Z")).toBe(whole + 123_000n);
    expect(micros("2026-10-07T12:00:00.000001Z")).toBe(whole + 1n);
    expect(micros("2026-10-07T12:00:00.123456789Z")).toBe(whole + 123_456n); // finer than we keep
  });
  it("treats missing or non-canonical values as the epoch instead of throwing", () => {
    expect(micros(null)).toBe(0n);
    expect(micros("yesterday")).toBe(0n);
    expect(micros("2026-10-07T12:00:00+02:00")).toBe(0n);
  });
});

describe("mergeEvents", () => {
  const a = ev("evt_a", 1, "2026-10-07T12:00:01Z");
  const b = ev("evt_b", 2, "2026-10-07T12:00:02Z");
  const c = ev("evt_c", 3, "2026-10-07T12:00:03Z");

  it("ignores duplicates and returns the same list when nothing is new", () => {
    const first = mergeEvents([], [a, b]);
    const again = mergeEvents(first.events, [b, a]);
    expect(again.added).toBe(0);
    expect(again.events).toBe(first.events);
  });
  it("puts a late event at its canonical position", () => {
    const merged = mergeEvents([a, c], [b]);
    expect(merged.events.map((e) => e.event_id)).toEqual(["evt_a", "evt_b", "evt_c"]);
    expect(merged.added).toBe(1);
  });
  it("appends without re-sorting when everything new is newest", () => {
    const merged = mergeEvents([a, b], [c]);
    expect(merged.events.map((e) => e.event_id)).toEqual(["evt_a", "evt_b", "evt_c"]);
  });
  it("switches to time ordering when an event without a sequence arrives", () => {
    const noSeq = ev("evt_x", null, "2026-10-07T12:00:00Z"); // earliest by time
    const merged = mergeEvents([a, b], [noSeq]);
    expect(merged.mode).toBe("time");
    expect(merged.events.map((e) => e.event_id)).toEqual(["evt_x", "evt_a", "evt_b"]);
  });
  it("re-sorts everything when a mode change makes the old order wrong", () => {
    // by sequence b(1) comes before a(2); by time a(t=1) comes before b(t=2)
    const early = ev("evt_a", 2, "2026-10-07T12:00:01Z");
    const later = ev("evt_b", 1, "2026-10-07T12:00:02Z");
    const bySequence = mergeEvents([], [early, later]);
    expect(bySequence.events.map((e) => e.event_id)).toEqual(["evt_b", "evt_a"]);
    const newest = ev("evt_x", null, "2026-10-07T12:00:03Z");
    const byTime = mergeEvents(bySequence.events, [newest]);
    expect(byTime.mode).toBe("time");
    expect(byTime.events.map((e) => e.event_id)).toEqual(["evt_a", "evt_b", "evt_x"]);
  });
  it("does not change the input", () => {
    const input = [a, c];
    mergeEvents(input, [b]);
    expect(input.map((e) => e.event_id)).toEqual(["evt_a", "evt_c"]);
  });
  it("breaks every tie on received_at, then event_id", () => {
    const x = ev("evt_2", 1, "2026-10-07T12:00:01Z", "2026-10-07T12:00:05Z");
    const y = ev("evt_1", 1, "2026-10-07T12:00:01Z", "2026-10-07T12:00:06Z");
    const z = ev("evt_0", 1, "2026-10-07T12:00:01Z", "2026-10-07T12:00:06Z");
    expect(sortEvents([y, x, z]).map((e) => e.event_id)).toEqual(["evt_2", "evt_0", "evt_1"]);
  });
});
