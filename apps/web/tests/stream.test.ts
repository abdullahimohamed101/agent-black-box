import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  defaultBackoff,
  openRunStream,
  parseEvent,
  streamUrl,
  type EventSourceLike,
  type StreamState,
} from "@/lib/stream";

class FakeSource implements EventSourceLike {
  readyState = 0;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  closed = false;
  private listeners = new Map<string, ((ev: MessageEvent) => void)[]>();
  constructor(public url: string) {}
  addEventListener(type: string, listener: (ev: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  open() {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }
  emit(type: string, data: unknown) {
    for (const l of this.listeners.get(type) ?? []) l({ data } as MessageEvent);
  }
  trace(id: string, extra: Record<string, unknown> = {}) {
    this.emit("trace_event", JSON.stringify(wire(id, extra)));
  }
  /** What a browser does after a dropped connection: reconnecting, will retry itself. */
  drop() {
    this.readyState = 0;
    this.onerror?.(new Event("error"));
  }
  /** What a browser does after an HTTP error: gives up for good. */
  fail() {
    this.readyState = 2;
    this.onerror?.(new Event("error"));
  }
}

const wire = (id: string, extra: Record<string, unknown> = {}) => ({
  event_id: id,
  run_id: "run_1",
  event_type: "tool.call.completed",
  occurred_at: "2026-10-07T12:00:00Z",
  sequence: 1,
  attributes: {},
  ...extra,
});

function harness(overrides: Partial<Parameters<typeof openRunStream>[0]> = {}) {
  const sources: FakeSource[] = [];
  const batches: string[][] = [];
  const states: StreamState[] = [];
  const flushes: (() => void)[] = [];
  let ended = 0;
  const handle = openRunStream({
    runId: "run_1",
    onEvents: (events) => batches.push(events.map((e) => e.event_id)),
    onState: (s) => states.push(s),
    onEnd: () => ended++,
    eventSource: (url) => {
      const s = new FakeSource(url);
      sources.push(s);
      return s;
    },
    schedule: (f) => {
      flushes.push(f);
      return () => {
        const i = flushes.indexOf(f);
        if (i >= 0) flushes.splice(i, 1);
      };
    },
    backoffMs: () => 1000,
    ...overrides,
  });
  const frame = () => flushes.splice(0).forEach((f) => f());
  return { handle, sources, batches, states, frame, ended: () => ended, flushes };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("url and parsing", () => {
  it("builds the proxied URL and encodes the resume point", () => {
    expect(streamUrl("run_1")).toBe("/api/abb/v1/runs/run_1/stream");
    expect(streamUrl("run_1", "evt_9")).toBe("/api/abb/v1/runs/run_1/stream?last_event_id=evt_9");
    expect(streamUrl("a/b", "x&y")).toBe("/api/abb/v1/runs/a%2Fb/stream?last_event_id=x%26y");
  });
  it("accepts only well-formed trace events", () => {
    expect(parseEvent(JSON.stringify(wire("evt_1")))?.event_id).toBe("evt_1");
    for (const bad of [
      undefined,
      "",
      "not json",
      "null",
      "[]",
      JSON.stringify({ ...wire("evt_1"), event_id: 5 }),
      JSON.stringify({ ...wire("evt_1"), occurred_at: undefined }),
      JSON.stringify({ ...wire("evt_1"), sequence: "1" }),
      JSON.stringify({ ...wire("evt_1"), attributes: null }),
      JSON.stringify({ ...wire("evt_1"), attributes: [] }),
      JSON.stringify({ ...wire("evt_1"), attributes: "x" }),
    ]) {
      expect(parseEvent(bad)).toBeNull();
    }
  });
  it("backs off exponentially up to 30 s", () => {
    expect([1, 2, 3, 4, 5, 6, 7, 8].map(defaultBackoff)).toEqual([
      1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000,
    ]);
  });
});

describe("openRunStream", () => {
  it("connects, reports live, and hands events over once per frame", () => {
    const h = harness({ lastEventId: "evt_0" });
    expect(h.sources[0]!.url).toBe("/api/abb/v1/runs/run_1/stream?last_event_id=evt_0");
    expect(h.states).toEqual(["connecting"]);
    h.sources[0]!.open();
    h.sources[0]!.trace("evt_1");
    h.sources[0]!.trace("evt_2");
    h.sources[0]!.trace("evt_3");
    expect(h.batches).toEqual([]); // nothing until the frame
    expect(h.flushes).toHaveLength(1); // one scheduled flush for the whole burst
    h.frame();
    expect(h.batches).toEqual([["evt_1", "evt_2", "evt_3"]]);
    expect(h.states).toEqual(["connecting", "live"]);
  });

  it("drops malformed events and keeps going", () => {
    const h = harness();
    h.sources[0]!.open();
    h.sources[0]!.emit("trace_event", "{broken");
    h.sources[0]!.emit("trace_event", JSON.stringify({ event_id: 1 }));
    h.sources[0]!.trace("evt_1");
    h.frame();
    expect(h.batches).toEqual([["evt_1"]]);
    expect(h.handle.malformed).toBe(2);
  });

  it("lets the browser reconnect by itself after a dropped connection", () => {
    const h = harness();
    h.sources[0]!.open();
    h.sources[0]!.drop();
    expect(h.states.at(-1)).toBe("reconnecting");
    expect(h.sources).toHaveLength(1); // the browser's own retry, not ours
    h.sources[0]!.open();
    expect(h.states.at(-1)).toBe("live");
  });

  it("reopens after an HTTP error from the last event it saw, with backoff", () => {
    const h = harness();
    h.sources[0]!.open();
    h.sources[0]!.trace("evt_1");
    h.sources[0]!.trace("evt_2");
    h.frame();
    h.sources[0]!.fail();
    expect(h.states.at(-1)).toBe("reconnecting");
    expect(h.sources).toHaveLength(1);
    vi.advanceTimersByTime(999);
    expect(h.sources).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(h.sources).toHaveLength(2);
    expect(h.sources[1]!.url).toBe("/api/abb/v1/runs/run_1/stream?last_event_id=evt_2");
    h.sources[1]!.open();
    expect(h.states.at(-1)).toBe("live");
  });

  it("reports unavailable after repeated failures so the caller can poll", () => {
    const h = harness({ maxFailures: 3 });
    for (let i = 0; i < 3; i++) {
      h.sources.at(-1)!.fail();
      vi.advanceTimersByTime(1000);
    }
    expect(h.states.at(-1)).toBe("unavailable");
    const count = h.sources.length;
    vi.advanceTimersByTime(60_000);
    expect(h.sources).toHaveLength(count); // and it stops trying
  });

  it("forgives earlier failures once a connection has stayed open or delivered an event", () => {
    const h = harness({ maxFailures: 2 });
    h.sources[0]!.fail();
    vi.advanceTimersByTime(1000);
    h.sources[1]!.open();
    vi.advanceTimersByTime(10_000); // healthy for stableMs
    h.sources[1]!.fail();
    vi.advanceTimersByTime(1000);
    expect(h.states.at(-1)).toBe("reconnecting");
    expect(h.sources).toHaveLength(3);
    h.sources[2]!.open();
    h.sources[2]!.trace("evt_1"); // delivering counts as healthy at once
    h.sources[2]!.fail();
    vi.advanceTimersByTime(1000);
    expect(h.sources).toHaveLength(4);
  });

  it("does not forgive an open that fails again at once", () => {
    const h = harness({ maxFailures: 2 });
    h.sources[0]!.fail();
    vi.advanceTimersByTime(1000);
    h.sources[1]!.open();
    h.sources[1]!.fail();
    expect(h.states.at(-1)).toBe("unavailable");
  });

  it("ends on run_end: flushes what arrived, closes, and does not reconnect", () => {
    const h = harness();
    h.sources[0]!.open();
    h.sources[0]!.trace("evt_1");
    h.sources[0]!.emit("run_end", '{"reason":"run_finished"}');
    expect(h.batches).toEqual([["evt_1"]]); // flushed without waiting for the frame
    expect(h.states.at(-1)).toBe("ended");
    expect(h.ended()).toBe(1);
    expect(h.sources[0]!.closed).toBe(true);
    h.sources[0]!.drop();
    vi.advanceTimersByTime(60_000);
    expect(h.sources).toHaveLength(1);
  });

  it("counts the server's in-band error frames as failures, so a faulty server ends in 'unavailable'", () => {
    const h = harness({ maxFailures: 3 });
    const es = h.sources[0]!; // the browser reconnects with this same object after each error
    for (let i = 0; i < 3; i++) {
      es.open();
      es.onerror?.({ data: '{"error":{"code":"STREAM_UNAVAILABLE"}}' } as unknown as Event);
      expect(h.states.at(-1)).toBe(i < 2 ? "reconnecting" : "unavailable");
    }
    expect(es.closed).toBe(true); // and the client stops the browser's own retrying
  });

  it("never gives up on plain network drops: the browser keeps retrying by itself", () => {
    const h = harness({ maxFailures: 2 });
    h.sources[0]!.open();
    for (let i = 0; i < 20; i++) h.sources[0]!.drop();
    expect(h.states.at(-1)).toBe("reconnecting");
    expect(h.sources[0]!.closed).toBe(false);
    expect(h.sources).toHaveLength(1);
  });

  it("close() stops everything and drops pending work", () => {
    const h = harness();
    h.sources[0]!.open();
    h.sources[0]!.trace("evt_1");
    h.handle.close();
    h.frame();
    expect(h.batches).toEqual([]);
    expect(h.sources[0]!.closed).toBe(true);
    h.sources[0]!.fail();
    vi.advanceTimersByTime(60_000);
    expect(h.sources).toHaveLength(1);
    h.handle.close(); // idempotent
  });

  it("close() cancels a scheduled retry", () => {
    const h = harness();
    h.sources[0]!.fail();
    h.handle.close();
    vi.advanceTimersByTime(60_000);
    expect(h.sources).toHaveLength(1);
  });
});
