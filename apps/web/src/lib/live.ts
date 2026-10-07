"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { EventOut } from "@/lib/api/types";
import { mergeEvents, micros } from "./ordering";
import { openRunStream, type StreamState } from "./stream";

/** After a gap in live delivery longer than this, reload through REST rather than trust the resume alone. */
export const RECONCILE_GAP_MS = 20_000;

export type LiveState = StreamState | "off";

export interface LiveOptions {
  /** Stream while true (a running run in a real deployment). */
  enabled: boolean;
  /** Events already loaded through REST; the stream resumes after the newest of them. */
  restEvents: readonly EventOut[];
  /** True once REST has paged the whole run, so resuming cannot leave a hole. */
  restComplete: boolean;
  /** Reload the REST events (reconciliation after a long gap or at the end of the run). */
  reconcile: () => void;
  onEnd?: () => void;
  /** Injected for tests. */
  now?: () => number;
}

/** The id of the event the server received last: where a stream resumes (ADR-022). */
export function newestArrival(events: readonly EventOut[]): string | null {
  let best: EventOut | null = null;
  let bestAt = 0n;
  for (const e of events) {
    const at = micros(e.received_at);
    if (best === null || at > bestAt) {
      best = e;
      bestAt = at;
    }
  }
  return best?.event_id ?? null;
}

/**
 * REST history plus live events, merged by event id into canonical order. Duplicates (the server resends a
 * window on every reconnect), late and out-of-order events are all handled by `mergeEvents`.
 */
export function useLiveEvents(
  runId: string,
  { enabled, restEvents, restComplete, reconcile, onEnd, now = Date.now }: LiveOptions,
) {
  const [live, setLive] = useState<EventOut[]>([]);
  const [state, setState] = useState<LiveState>("off");
  const rest = useRef(restEvents);
  const callbacks = useRef({ reconcile, onEnd, now });
  useEffect(() => {
    rest.current = restEvents;
    callbacks.current = { reconcile, onEnd, now };
  });

  useEffect(() => {
    if (!enabled || !restComplete) return;
    let lostAt: number | null = null;
    let previous: StreamState | null = null;
    const handle = openRunStream({
      runId,
      lastEventId: newestArrival(rest.current),
      onEvents: (batch) => setLive((prev) => mergeEvents(prev, batch).events),
      onState: (s) => {
        setState(s);
        const t = callbacks.current.now();
        if (s === "live") {
          if (lostAt !== null && previous !== "connecting" && t - lostAt > RECONCILE_GAP_MS) {
            callbacks.current.reconcile();
          }
          lostAt = null;
        } else if (s === "reconnecting" || s === "unavailable") {
          lostAt ??= t;
        }
        previous = s;
      },
      onEnd: () => {
        callbacks.current.reconcile();
        callbacks.current.onEnd?.();
      },
    });
    return () => {
      handle.close();
      setState("off");
    };
  }, [runId, enabled, restComplete]);

  const events = useMemo(
    () => (live.length === 0 ? restEvents : mergeEvents(restEvents, live).events),
    [restEvents, live],
  );
  return { events, state: enabled ? state : ("off" as LiveState) };
}
