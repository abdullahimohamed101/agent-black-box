import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EventOut } from "@/lib/api/types";
import { useLiveEvents } from "@/lib/live";

class Source {
  static all: Source[] = [];
  readyState = 1;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  private listeners = new Map<string, ((ev: MessageEvent) => void)[]>();
  constructor(readonly url: string) {
    Source.all.push(this);
  }
  addEventListener(type: string, l: (ev: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), l]);
  }
  close() {
    this.readyState = 2;
  }
  trace(e: EventOut) {
    act(() => {
      for (const l of this.listeners.get("trace_event") ?? [])
        l({ data: JSON.stringify(e) } as MessageEvent);
    });
  }
}

const ev = (n: number): EventOut =>
  ({
    event_id: `evt_${n}`,
    run_id: "run_1",
    event_type: "tool.call.completed",
    occurred_at: `2026-10-07T12:00:0${n}Z`,
    received_at: `2026-10-07T12:00:0${n}Z`,
    sequence: n,
    attributes: {},
  }) as EventOut;

beforeEach(() => {
  Source.all = [];
  vi.stubGlobal("EventSource", Source);
  vi.stubGlobal("requestAnimationFrame", (f: () => void) => setTimeout(f, 0));
  vi.stubGlobal("cancelAnimationFrame", (id: number) => clearTimeout(id));
});
afterEach(() => vi.unstubAllGlobals());

describe("useLiveEvents state", () => {
  it("drops live copies of events REST already has, so it does not grow without bound", async () => {
    const rest = [ev(1), ev(2)];
    const { result } = renderHook(() =>
      useLiveEvents("run_1", {
        enabled: true,
        restEvents: rest,
        restComplete: true,
        reconcile: () => {},
      }),
    );
    const source = Source.all[0]!;
    source.trace(ev(2)); // the resume overlap repeats an event REST has
    source.trace(ev(3));
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(result.current.events.map((e) => e.event_id)).toEqual(["evt_1", "evt_2", "evt_3"]);
    const first = result.current.liveCount;
    source.trace(ev(4));
    await act(() => new Promise((r) => setTimeout(r, 20)));
    // evt_2 (known to REST) was pruned from state by the second batch; evt_3 and evt_4 remain
    expect(result.current.liveCount).toBe(2);
    expect(first).toBeGreaterThanOrEqual(2);
    expect(result.current.events).toHaveLength(4);
  });

  it("reports 'off' and opens nothing while disabled or before the history is complete", () => {
    const { result, rerender } = renderHook(
      ({ complete }) =>
        useLiveEvents("run_1", {
          enabled: true,
          restEvents: [],
          restComplete: complete,
          reconcile: () => {},
        }),
      { initialProps: { complete: false } },
    );
    expect(Source.all).toHaveLength(0);
    expect(result.current.state).toBe("off");
    rerender({ complete: true });
    expect(Source.all).toHaveLength(1);
  });
});
