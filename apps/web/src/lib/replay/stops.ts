import type { EventOut } from "@/lib/api/types";

/**
 * Step units (Phase 12, D2). By default a request's started and completed events (consecutive events sharing a span,
 * the same rule as the timeline's collapsible groups in `buildRows`) are one stop; with grouping off every event is.
 * A stop is the inclusive index range `start..end`; stepping lands on `end`, so the group has finished when you arrive.
 */
export type Stop = { start: number; end: number };

export function groupStops(events: readonly EventOut[], grouped: boolean = true): Stop[] {
  const stops: Stop[] = [];
  let i = 0;
  while (i < events.length) {
    let j = i;
    const span = grouped ? events[i]!.span_id : null;
    if (span) while (j + 1 < events.length && events[j + 1]!.span_id === span) j++;
    stops.push({ start: i, end: j });
    i = j + 1;
  }
  return stops;
}

/** Index of the first stop whose end is greater than `index`, or stops.length. */
function firstEndAfter(stops: readonly Stop[], index: number): number {
  let lo = 0;
  let hi = stops.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (stops[mid]!.end > index) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}

/** The playhead after "step forward": the end of the next stop (the last event when there is none). */
export function stepForward(stops: readonly Stop[], index: number): number {
  const k = firstEndAfter(stops, index);
  return k < stops.length ? stops[k]!.end : Math.max(index, stops[stops.length - 1]?.end ?? index);
}

/** The playhead after "step back": the end of the stop before the one the playhead is in (the first event at most). */
export function stepBack(stops: readonly Stop[], index: number): number {
  // Stops whose end is below the playhead are all behind it; the last of them is the previous stop.
  let lo = 0;
  let hi = stops.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (stops[mid]!.end >= index) hi = mid;
    else lo = mid + 1;
  }
  return lo > 0 ? stops[lo - 1]!.end : 0;
}
