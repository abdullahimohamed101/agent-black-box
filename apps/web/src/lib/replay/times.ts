import type { EventOut } from "@/lib/api/types";

/**
 * Event times as milliseconds, parsed once per event list. NaN where a timestamp is missing or unparseable (captured
 * telemetry is hostile input). Cached by array identity: the run page hands the same array to every replay consumer,
 * and a new array (a live event arriving) is parsed again.
 */
const cache = new WeakMap<readonly EventOut[], Float64Array>();

export function eventTimes(events: readonly EventOut[]): Float64Array {
  let t = cache.get(events);
  if (!t) {
    t = new Float64Array(events.length);
    for (let i = 0; i < events.length; i++) {
      const ms = Date.parse(events[i]!.occurred_at);
      t[i] = Number.isFinite(ms) ? ms : NaN;
    }
    cache.set(events, t);
  }
  return t;
}
