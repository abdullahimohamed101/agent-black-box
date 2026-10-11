import type { EventOut } from "@/lib/api/types";
import registry from "@/lib/generated/event-types.json";
import { sortEvents } from "./ordering";

/**
 * Span derivation in the browser: a port of `abb_event_schema.spans.derive_spans` (spec §16, §65.3), pinned to the Python
 * implementation by `packages/event-schema/tests/data/spans-golden.json`. A `*.started` event opens a span, its closer
 * ends it and every other event attaches to it as a point. Nesting follows `parent_span_id` only, never time.
 *
 * Span roles and kinds come from the generated registry copy (`make schema`), so there is one source of truth. An event
 * type the registry does not know (`agent.failed`) is a point event: it closes nothing, exactly as on the server.
 */

type Role = "open" | "close" | "point";
type Spec = { role: Role; kind: string | null };

const SPECS: ReadonlyMap<string, Spec> = new Map(
  registry.event_types.map((t) => [
    t.event_type,
    { role: t.span_role as Role, kind: t.span_kind ?? null },
  ]),
);

const roleOf = (e: SpanEvent): Role => SPECS.get(e.event_type)?.role ?? "point";

/** The fields `derive_spans` reads; a structural subset so tests and the golden file can pass plain objects. */
export type SpanEvent = Pick<
  EventOut,
  | "event_id"
  | "event_type"
  | "span_id"
  | "parent_span_id"
  | "agent_id"
  | "occurred_at"
  | "status"
  | "duration_ms"
  | "attributes"
> &
  Partial<Pick<EventOut, "sequence" | "received_at" | "withheld_attributes">>;

export type DerivedSpan<E extends SpanEvent = EventOut> = {
  spanId: string;
  parentSpanId: string | null;
  agentId: string;
  /** Registered span kind of the opener (or first event); null for unregistered types. */
  kind: string | null;
  startedAt: string | null;
  endedAt: string | null;
  status: string | null;
  durationMs: number | null;
  eventCount: number;
  /** Timestamp of the first and last member in canonical order, for spans with no opener or closer. */
  firstAt: string;
  lastAt: string;
  /** The opener, else the first member: the event a click selects and a label is read from. */
  head: E;
  closer: E | null;
  hasError: boolean;
  /**
   * `derive_spans`' display name, read from raw attributes. Kept only for parity with the server; every label,
   * tooltip and aggregate shown to a person is built by `spanLabel`, which goes through `contentText` so a
   * withheld value never appears.
   */
  rawName: string | null;
};

const NAME_KEYS = ["span.name", "tool.name", "shell.command", "llm.model", "file.path"] as const;

function rawName(e: SpanEvent): string | null {
  for (const key of NAME_KEYS) {
    const v = e.attributes[key];
    if (typeof v === "string") return v;
  }
  return null;
}

const failed = (e: SpanEvent): boolean =>
  e.status === "error" || e.status === "timeout" || e.event_type.endsWith(".failed");

/** One span from its members, which must already be in canonical order. */
function deriveOne<E extends SpanEvent>(spanId: string, group: readonly E[]): DerivedSpan<E> {
  const first = group[0]!;
  let opener: E | null = null;
  let closer: E | null = null;
  let parent: string | null = null;
  let hasError = false;
  for (const e of group) {
    const role = roleOf(e);
    if (role === "open" && opener === null) opener = e;
    if (role === "close") closer = e; // the last closer in canonical order wins
    if (parent === null && e.parent_span_id) parent = e.parent_span_id;
    if (!hasError && failed(e)) hasError = true;
  }
  const head = opener ?? first;
  return {
    spanId,
    parentSpanId: parent,
    agentId: head.agent_id,
    kind: SPECS.get(head.event_type)?.kind ?? null,
    startedAt: opener ? opener.occurred_at : null,
    endedAt: closer ? closer.occurred_at : null,
    status: closer ? (closer.status ?? null) : null,
    durationMs: closer ? (closer.duration_ms ?? null) : null,
    eventCount: group.length,
    firstAt: first.occurred_at,
    lastAt: group[group.length - 1]!.occurred_at,
    head,
    closer,
    hasError,
    rawName: rawName(head),
  };
}

/** Group events by span id in first-appearance order (events must be canonical). */
function groupBySpan<E extends SpanEvent>(ordered: readonly E[]): Map<string, E[]> {
  const groups = new Map<string, E[]>();
  for (const e of ordered) {
    if (!e.span_id) continue;
    const g = groups.get(e.span_id);
    if (g) g.push(e);
    else groups.set(e.span_id, [e]);
  }
  return groups;
}

/** From-scratch derivation. Sorts like the server, so any arrival order gives the same spans. */
export function deriveSpans<E extends SpanEvent>(
  events: readonly E[],
): Map<string, DerivedSpan<E>> {
  const spans = new Map<string, DerivedSpan<E>>();
  for (const [id, group] of groupBySpan(sortEvents(events))) spans.set(id, deriveOne(id, group));
  return spans;
}

/**
 * A persistent span index for a growing event list (live merge, paging). `update` takes the whole canonical-ordered
 * array, walks it once (O(n), the same order as `mergeEvents`) and re-derives only the spans whose member list changed
 * (a new member, a member removed, or a member whose canonical position moved); the rest are reused by identity.
 * The result is always equal to `deriveSpans` of the same events.
 */
export class SpanIndex<E extends SpanEvent = EventOut> {
  private groups = new Map<string, readonly E[]>();
  private spans = new Map<string, DerivedSpan<E>>();
  private owner = new Map<string, string>();
  /** How many spans the last `update` re-derived; tests and the benchmark read it. */
  rederived = 0;

  update(ordered: readonly E[]): DerivedSpan<E>[] {
    const next = groupBySpan(ordered);
    const nextSpans = new Map<string, DerivedSpan<E>>();
    const owner = new Map<string, string>();
    let rederived = 0;
    for (const [id, group] of next) {
      const prev = this.groups.get(id);
      let same = prev !== undefined && prev.length === group.length;
      if (same) {
        for (let i = 0; i < group.length; i++) {
          if (prev![i]!.event_id !== group[i]!.event_id) {
            same = false;
            break;
          }
        }
      }
      if (same) nextSpans.set(id, this.spans.get(id)!);
      else {
        nextSpans.set(id, deriveOne(id, group));
        rederived++;
      }
      for (const e of group) owner.set(e.event_id, id);
    }
    this.groups = next;
    this.spans = nextSpans;
    this.owner = owner;
    this.rederived = rederived;
    return [...nextSpans.values()];
  }

  /** The span an event belongs to, if it has one. */
  spanIdOf(eventId: string): string | undefined {
    return this.owner.get(eventId);
  }
}
