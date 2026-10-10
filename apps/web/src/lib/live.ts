"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { EventOut } from "@/lib/api/types";
import { mergeEvents, micros } from "./ordering";
import { api } from "./api/client";
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

/**
 * After `STREAM_UNAUTHORIZED`: ask who we are again. A dead session goes to sign-in (the client's 401 handler); a session
 * that is fine but lost this run (role change) keeps the page, with live updates stopped.
 */
async function recheckSession(): Promise<void> {
  try {
    await api.GET("/v1/me"); // a 401 SESSION_INVALID runs the client's sign-in redirect itself
  } catch {
    /* offline: the stopped-live notice already tells the story */
  }
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
    let knownFor: readonly EventOut[] | null = null;
    let known = new Set<string>();
    const handle = openRunStream({
      runId,
      lastEventId: newestArrival(rest.current),
      onEvents: (batch) => {
        // Live copies of events REST already holds are redundant: drop them so state stays bounded.
        if (knownFor !== rest.current) {
          knownFor = rest.current;
          known = new Set(rest.current.map((e) => e.event_id));
        }
        setLive(
          (prev) =>
            mergeEvents(
              prev.filter((e) => !known.has(e.event_id)),
              batch,
            ).events,
        );
      },
      onState: (s) => {
        setState(s);
        if (s === "unauthorized") void recheckSession();
        const t = callbacks.current.now();
        if (s === "live") {
          if (lostAt !== null && t - lostAt > RECONCILE_GAP_MS) {
            callbacks.current.reconcile();
          }
          lostAt = null;
        } else if (s === "reconnecting" || s === "unavailable") {
          lostAt ??= t;
        }
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
  return { events, state: enabled ? state : ("off" as LiveState), liveCount: live.length };
}
