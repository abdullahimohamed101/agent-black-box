import { useMemo, useState } from "react";
import type { EventOut } from "@/lib/api/types";
import { SpanIndex, type DerivedSpan } from "./spans";

export type Spans = { spans: DerivedSpan[]; spanIdOf: (eventId: string) => string | undefined };
const NONE: Spans = { spans: [], spanIdOf: () => undefined };

/**
 * The run's spans, derived from the events the page already holds. The index is persistent, so a live frame
 * re-derives only the spans it touched. Off (the timeline view) it costs nothing.
 */
export function useSpans(events: readonly EventOut[], enabled: boolean): Spans {
  const [index] = useState(() => new SpanIndex());
  return useMemo(() => {
    if (!enabled) return NONE;
    const spans = index.update(events);
    return { spans, spanIdOf: (id: string) => index.spanIdOf(id) };
  }, [index, events, enabled]);
}
