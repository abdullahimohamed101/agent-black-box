import type { EventOut } from "@/lib/api/types";

/**
 * Browser side of `GET /v1/runs/{id}/stream` (spec §76, ADR-022), through the same-origin read proxy.
 *
 * - Events are batched and handed over once per frame, so a burst re-renders once.
 * - The server repeats a window of already-seen events on every (re)connect; consumers de-duplicate by
 *   `event_id` (`mergeEvents`), this client does not try to.
 * - `EventSource` retries by itself after a dropped connection (sending `Last-Event-ID`), but gives up
 *   for good on an HTTP error. Then this client reopens with backoff from the last event it saw, and
 *   after `maxFailures` consecutive failures reports `unavailable` so the caller can poll instead.
 */

export type StreamState = "connecting" | "live" | "reconnecting" | "ended" | "unavailable";

export type StreamEvent = EventOut;

export interface StreamOptions {
  runId: string;
  /** Newest event the caller already holds; the first connection resumes after it. */
  lastEventId?: string | null;
  onEvents: (events: StreamEvent[]) => void;
  onState?: (state: StreamState) => void;
  /** Server said the run is finished and quiet: no more events unless the caller reconnects. */
  onEnd?: () => void;
  /** Injected for tests; defaults to the browser's EventSource. */
  eventSource?: (url: string) => EventSourceLike;
  /** Injected for tests; defaults to requestAnimationFrame / setTimeout. */
  schedule?: (flush: () => void) => () => void;
  maxFailures?: number;
  backoffMs?: (failures: number) => number;
}

export interface EventSourceLike {
  readonly readyState: number;
  onopen: ((ev: Event) => void) | null;
  onerror: ((ev: Event) => void) | null;
  addEventListener(type: string, listener: (ev: MessageEvent) => void): void;
  close(): void;
}

const CONNECTING = 0;
const CLOSED = 2;

export const streamUrl = (runId: string, lastEventId?: string | null): string => {
  const base = `/api/abb/v1/runs/${encodeURIComponent(runId)}/stream`;
  return lastEventId ? `${base}?last_event_id=${encodeURIComponent(lastEventId)}` : base;
};

/** A wire event is only trusted for the fields the timeline orders and renders by. */
export function parseEvent(data: unknown): StreamEvent | null {
  if (typeof data !== "string") return null;
  try {
    const e: unknown = JSON.parse(data);
    if (typeof e !== "object" || e === null) return null;
    const o = e as Record<string, unknown>;
    const ok =
      typeof o.event_id === "string" &&
      typeof o.event_type === "string" &&
      typeof o.occurred_at === "string" &&
      typeof o.run_id === "string" &&
      (o.sequence === null || o.sequence === undefined || typeof o.sequence === "number");
    return ok ? (e as StreamEvent) : null;
  } catch {
    return null;
  }
}

const defaultSchedule = (flush: () => void): (() => void) => {
  if (typeof requestAnimationFrame === "function") {
    const id = requestAnimationFrame(flush);
    return () => cancelAnimationFrame(id);
  }
  const id = setTimeout(flush, 16);
  return () => clearTimeout(id);
};

export const defaultBackoff = (failures: number): number =>
  Math.min(30_000, 1000 * 2 ** (failures - 1));

export interface StreamHandle {
  close: () => void;
  /** Events dropped because they were not valid trace events (should stay 0). */
  readonly malformed: number;
}

export function openRunStream(options: StreamOptions): StreamHandle {
  const {
    runId,
    onEvents,
    onState = () => {},
    onEnd = () => {},
    schedule = defaultSchedule,
    maxFailures = 8,
    backoffMs = defaultBackoff,
  } = options;
  const create =
    options.eventSource ?? ((url: string) => new EventSource(url) as unknown as EventSourceLike);

  let source: EventSourceLike | null = null;
  let lastId: string | null = options.lastEventId ?? null;
  let buffer: StreamEvent[] = [];
  let cancelFlush: (() => void) | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let failures = 0;
  let closed = false;
  let malformed = 0;

  const flush = () => {
    cancelFlush = null;
    if (buffer.length === 0) return;
    const batch = buffer;
    buffer = [];
    onEvents(batch);
  };
  const queue = (event: StreamEvent) => {
    buffer.push(event);
    cancelFlush ??= schedule(flush);
  };
  const finish = (state: StreamState) => {
    cancelFlush?.();
    flush(); // never lose what already arrived
    source?.close();
    source = null;
    closed = true;
    onState(state);
  };

  const connect = () => {
    if (closed) return;
    onState(
      failures === 0 && lastId === (options.lastEventId ?? null) ? "connecting" : "reconnecting",
    );
    const es = create(streamUrl(runId, lastId));
    source = es;
    es.onopen = () => {
      failures = 0;
      onState("live");
    };
    es.addEventListener("trace_event", (message) => {
      const event = parseEvent(message.data);
      if (event === null) {
        malformed++;
        return;
      }
      lastId = event.event_id;
      queue(event);
    });
    es.addEventListener("run_end", () => {
      finish("ended");
      onEnd();
    });
    es.onerror = (ev) => {
      if (closed || source !== es) return;
      // The server's own `event: error` frame arrives on this same channel, carrying data: it closes
      // the stream and the browser reconnects by itself, like any dropped connection.
      if (es.readyState === CONNECTING || (ev as MessageEvent).data !== undefined) {
        onState("reconnecting");
        return;
      }
      if (es.readyState === CLOSED) {
        // An HTTP error (429 STREAM_LIMIT, 503, ...): EventSource will not retry. Do it ourselves.
        es.close();
        source = null;
        failures++;
        if (failures >= maxFailures) {
          finish("unavailable");
          return;
        }
        onState("reconnecting");
        retryTimer = setTimeout(connect, backoffMs(failures));
      }
    };
  };

  if (!options.eventSource && typeof EventSource === "undefined") {
    // No SSE in this environment: say so, and the caller polls.
    closed = true;
    onState("unavailable");
  } else {
    connect();
  }
  return {
    close: () => {
      if (closed) return;
      if (retryTimer) clearTimeout(retryTimer);
      cancelFlush?.();
      source?.close();
      source = null;
      closed = true;
    },
    get malformed() {
      return malformed;
    },
  };
}
