import type { EventOut } from "@/lib/api/types";

/**
 * Canonical event order, mirroring `abb_event_schema.ordering` (spec §65.1): when every event has a
 * `sequence`, order by (sequence, occurred_at, received_at, event_id); otherwise by
 * (occurred_at, sequence, received_at, event_id). A golden file generated from the Python
 * implementation (`packages/event-schema/tests/data/ordering-golden.json`) pins the two together.
 *
 * Live streaming merges events into the timeline in the browser, so this has to agree with the server.
 */

export type Orderable = Pick<EventOut, "event_id" | "occurred_at"> & {
  sequence?: number | null;
  received_at?: string | null;
};
export type OrderingMode = "sequence" | "time";

const WIRE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?Z$/;

/** Microseconds since the epoch. JS dates stop at milliseconds, which would reorder events that differ by less. */
export function micros(timestamp: string | null | undefined): bigint {
  if (!timestamp) return 0n; // a missing received_at sorts first, as Python's epoch default does
  const m = WIRE.exec(timestamp);
  // The API only emits canonical UTC timestamps (`...Z`), which the golden file covers. Anything else, such as an
  // offset (Python would convert it), is not trusted to order and sorts first instead of throwing.
  if (!m) return 0n;
  const [, y, mo, d, h, mi, s, frac = ""] = m;
  const seconds =
    Date.UTC(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s)) / 1000;
  return BigInt(seconds) * 1_000_000n + BigInt(frac.padEnd(6, "0").slice(0, 6));
}

const cmp = <T extends bigint | number | string>(a: T, b: T): number =>
  a < b ? -1 : a > b ? 1 : 0;

export function orderingMode(events: readonly Orderable[]): OrderingMode {
  return events.length > 0 && events.every((e) => e.sequence != null) ? "sequence" : "time";
}

export function compareEvents(mode: OrderingMode): (a: Orderable, b: Orderable) => number {
  return (a, b) => {
    const seq = cmp(a.sequence ?? 0, b.sequence ?? 0);
    const occurred = cmp(micros(a.occurred_at), micros(b.occurred_at));
    const received = cmp(micros(a.received_at), micros(b.received_at));
    const first = mode === "sequence" ? seq || occurred : occurred || seq;
    return first || received || cmp(a.event_id, b.event_id);
  };
}

export function sortEvents<T extends Orderable>(events: readonly T[]): T[] {
  return [...events].sort(compareEvents(orderingMode(events)));
}

export type Merged<T> = { events: T[]; mode: OrderingMode; added: number };

/**
 * Add `incoming` to an already ordered list. Events seen before (same `event_id`) are ignored, so
 * duplicate delivery and the server's resume overlap are harmless; late and out-of-order events land
 * at their canonical position, and one event without a sequence moves everything to time ordering.
 */
export function mergeEvents<T extends Orderable>(
  existing: readonly T[],
  incoming: readonly T[],
): Merged<T> {
  const seen = new Set(existing.map((e) => e.event_id));
  const fresh: T[] = [];
  for (const e of incoming) {
    if (!seen.has(e.event_id)) {
      seen.add(e.event_id);
      fresh.push(e);
    }
  }
  const mode = orderingMode(fresh.length === 0 ? existing : [...existing, ...fresh]);
  if (fresh.length === 0) return { events: existing as T[], mode, added: 0 };
  const compare = compareEvents(mode);
  const sortedFresh = fresh.sort(compare);
  // Common live case: everything new sorts after what we have, so appending avoids a full re-sort.
  const last = existing[existing.length - 1];
  const modeUnchanged = existing.length === 0 || orderingMode(existing) === mode;
  if (modeUnchanged && (last === undefined || compare(last, sortedFresh[0]!) <= 0)) {
    return { events: [...existing, ...sortedFresh], mode, added: fresh.length };
  }
  return { events: [...existing, ...sortedFresh].sort(compare), mode, added: fresh.length };
}
