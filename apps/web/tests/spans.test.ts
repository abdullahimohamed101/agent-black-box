import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { mergeEvents, micros, sortEvents } from "@/lib/ordering";
import { deriveSpans, SpanIndex, type DerivedSpan, type SpanEvent } from "@/lib/spans";

type Expected = {
  span_id: string;
  parent_span_id: string | null;
  agent_id: string;
  name: string | null;
  kind: string | null;
  started_at: string | null;
  ended_at: string | null;
  status: string | null;
  duration_ms: number | null;
  event_count: number;
};
type Golden = {
  cases: { name: string; events: SpanEvent[]; expected: Expected[] }[];
  generated: {
    name: string;
    generator: { length: number; event_pair: [string, string]; step_ms: number };
    expected_count: number;
    expected_first: Expected;
    expected_last: Expected;
  }[];
};
const golden: Golden = JSON.parse(
  readFileSync(
    path.resolve(__dirname, "../../../packages/event-schema/tests/data/spans-golden.json"),
    "utf8",
  ),
);

const shape = (s: DerivedSpan<SpanEvent>): Expected => ({
  span_id: s.spanId,
  parent_span_id: s.parentSpanId,
  agent_id: s.agentId,
  name: s.rawName,
  kind: s.kind,
  started_at: s.startedAt,
  ended_at: s.endedAt,
  status: s.status,
  duration_ms: s.durationMs,
  event_count: s.eventCount,
});

const named = (name: string) => golden.cases.find((c) => c.name === name)!;

describe("parity with the Python derive_spans", () => {
  it("has the golden cases", () => {
    expect(golden.cases.length).toBeGreaterThanOrEqual(16);
    expect(golden.generated).toHaveLength(1);
  });

  it.each(golden.cases.map((c) => [c.name, c] as const))("%s derives the same spans", (_n, c) => {
    // Python's dict order is first appearance in canonical order; the Map keeps the same order.
    expect([...deriveSpans(c.events).values()].map(shape)).toEqual(c.expected);
  });

  it.each(golden.cases.map((c) => [c.name, c] as const))(
    "%s: the arrival order does not matter",
    (_n, c) => {
      const reversed = [...c.events].reverse();
      expect([...deriveSpans(reversed).values()].map(shape)).toEqual(c.expected);
    },
  );

  it("an unregistered closer (agent.failed) closes nothing", () => {
    const [span] = [...deriveSpans(named("unregistered closer").events).values()];
    expect(span?.endedAt).toBeNull();
    expect(span?.eventCount).toBe(2);
    expect(span?.hasError).toBe(true); // it is still an error to a reader
  });

  it("a cycle yields both spans and nesting follows parent_span_id, not time", () => {
    const spans = deriveSpans(named("cycle").events);
    expect(spans.size).toBe(2);
    const skew = [...deriveSpans(named("skewed child").events).values()];
    const parent = skew.find((s) => s.agentId === "parent-agent")!;
    const child = skew.find((s) => s.agentId === "child-agent")!;
    expect(parent.parentSpanId).toBeNull();
    expect(child.parentSpanId).toBe(parent.spanId); // the child's clock precedes its parent's
    expect(Date.parse(child.startedAt!)).toBeLessThan(Date.parse(parent.startedAt!));
  });

  it("each registered kind maps to its span kind", () => {
    const kinds = [...deriveSpans(named("one span per registered kind").events).values()].map(
      (s) => s.kind,
    );
    expect(new Set(kinds)).toEqual(new Set(["agent", "llm", "tool", "shell", "db", "custom"]));
  });
});

/** The deep chain, rebuilt from its generator description exactly as the Python test builds it. */
function deepChain(length: number, [open, close]: [string, string]): SpanEvent[] {
  const base = Date.parse("2026-10-07T12:00:00Z");
  const iso = (ms: number) => new Date(base + ms).toISOString();
  const id = (prefix: string, n: number) => `${prefix}_${String(n).padStart(26, "0")}`;
  const spn = (n: number) => `spn_01J9${String(n).padStart(22, "0")}`;
  const events: SpanEvent[] = [];
  let n = 0;
  for (let i = 1; i <= length; i++) {
    const parent = i > 1 ? spn(i - 1) : null;
    const start = i;
    const end = 2 * length - i + 1;
    const common = {
      span_id: spn(i),
      parent_span_id: parent,
      agent_id: "root",
      attributes: { "tool.name": "d" },
    };
    events.push({
      ...common,
      event_id: id("evt", ++n),
      event_type: open,
      occurred_at: iso(start),
      status: null,
      duration_ms: null,
    });
    events.push({
      ...common,
      event_id: id("evt", ++n),
      event_type: close,
      occurred_at: iso(end),
      status: "success",
      duration_ms: end - start,
    });
  }
  return events;
}

describe("generated golden cases", () => {
  it("the 10,000-deep chain derives every span without recursion", () => {
    const g = golden.generated[0]!;
    const spans = [
      ...deriveSpans(deepChain(g.generator.length, g.generator.event_pair)).values(),
    ].map((s) => shape(s as DerivedSpan<SpanEvent>));
    expect(spans).toHaveLength(g.expected_count);
    for (const [got, want] of [
      [spans[0]!, g.expected_first],
      [spans.at(-1)!, g.expected_last],
    ] as const) {
      expect(got.span_id).toBe(want.span_id);
      expect(got.parent_span_id).toBe(want.parent_span_id);
      expect(got.kind).toBe(want.kind);
      expect(got.event_count).toBe(want.event_count);
      expect(micros(got.started_at)).toBe(micros(want.started_at));
      expect(micros(got.ended_at)).toBe(micros(want.ended_at));
      expect(got.duration_ms!).toBeCloseTo(want.duration_ms!, 0);
    }
  });
});

describe("SpanIndex", () => {
  const random = golden.cases.find((c) => c.name === "random")!;

  it("20 merged batches equal a from-scratch derivation", () => {
    // A deterministic shuffle so batches arrive out of order and late events move earlier spans.
    const events = [...random.events];
    let seed = 7;
    for (let i = events.length - 1; i > 0; i--) {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      const j = seed % (i + 1);
      [events[i], events[j]] = [events[j]!, events[i]!];
    }
    const index = new SpanIndex<SpanEvent>();
    let merged: SpanEvent[] = [];
    const size = Math.ceil(events.length / 20);
    for (let b = 0; b < 20; b++) {
      merged = mergeEvents(merged, events.slice(b * size, (b + 1) * size)).events;
      const incremental = index.update(merged).map(shape);
      const scratch = [...deriveSpans(merged).values()].map(shape);
      expect(incremental).toEqual(scratch);
    }
    expect(index.update(merged).map(shape)).toEqual(random.expected);
  });

  it("re-derives only the spans whose events changed, and reuses the others", () => {
    const events = sortEvents(named("fan-out").events);
    const index = new SpanIndex<SpanEvent>();
    const first = index.update(events);
    expect(index.rederived).toBe(first.length);
    const again = index.update([...events]);
    expect(index.rederived).toBe(0);
    expect(again.every((s, i) => s === first[i])).toBe(true);
    // one late event for one span: exactly that span is re-derived
    const late: SpanEvent = {
      ...events[0]!,
      event_id: "evt_late",
      event_type: "custom.note.added",
      occurred_at: "2026-10-07T12:00:05Z",
    };
    const next = index.update(sortEvents([...events, late]));
    expect(index.rederived).toBe(1);
    expect(next.find((s) => s.spanId === events[0]!.span_id)?.eventCount).toBe(3);
    expect(index.spanIdOf("evt_late")).toBe(events[0]!.span_id);
  });

  it("a span whose member moved position is re-derived", () => {
    const events = sortEvents(named("close before open").events);
    const index = new SpanIndex<SpanEvent>();
    index.update(events);
    const [a, b] = events;
    // the same two events with swapped times: same ids, different canonical order
    const swapped = sortEvents([
      { ...a!, occurred_at: b!.occurred_at },
      { ...b!, occurred_at: a!.occurred_at },
    ]);
    const spans = index.update(swapped);
    expect(index.rederived).toBe(1);
    expect(spans.map(shape)).toEqual([...deriveSpans(swapped).values()].map(shape));
  });

  it("drops spans whose events are gone", () => {
    const events = sortEvents(named("chain").events);
    const index = new SpanIndex<SpanEvent>();
    index.update(events);
    const only = events.filter((e) => e.span_id === events[0]!.span_id);
    expect(index.update(only)).toHaveLength(1);
    const gone = events.find((e) => e.span_id !== events[0]!.span_id)!;
    expect(index.spanIdOf(gone.event_id)).toBeUndefined();
  });
});
