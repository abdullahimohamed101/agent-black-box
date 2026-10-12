"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { EventOut } from "@/lib/api/types";
import { describe } from "../timeline";
import { ReplayPacer, SPEEDS, type Speed } from "./pacing";
import { ReplayProjection, type ReplayCtx, type ReplayState } from "./project";
import { groupStops, stepBack, stepForward, type Stop } from "./stops";

/**
 * The replay player (Phase 12, D4): playhead, play/pause, speed, the URL (`?t=<event id>`), pause at the first error and
 * the keyboard map. The playhead is an index into the page's canonical event order; `state` is the projection there, so
 * every panel reads one source. Nothing here executes anything or touches the network: it only moves an index.
 *
 * Playback advances one event at a time, each after its paced delay (`ReplayPacer`), chained with `setTimeout` so a slow
 * render can never pile ticks up. "Group steps" only changes what step forward/back and the arrow keys do.
 *
 * The URL is written when playback pauses and when the person seeks, steps, enters or leaves (never on a tick), through
 * `history.replaceState` like the run page's own `?view=`: Next keeps `useSearchParams` in sync with it and, unlike
 * `router.replace`, it does not refetch the server component, so replay really makes no request. Writes are trailing-
 * debounced (a dragged scrub bar would otherwise hit Safari's replaceState rate limit). A URL that changes underneath us
 * (back/forward) is adopted: an id enters replay paused at that event, no `t` leaves replay.
 *
 * SSR/hydration: the first render reads only `useSearchParams`, never `window`, so it equals the server's.
 */

const URL_DEBOUNCE_MS = 150;
const MODE_KEYS = new Set([" ", "ArrowLeft", "ArrowRight", "[", "]", "Escape"]);
/** Where Space and the arrows already mean something: a control being operated, or a widget with its own keys. */
const OWN_KEYS =
  '[role="tablist"],[role="menu"],[role="menubar"],[role="radiogroup"],[role="slider"],input[type="range"],input[type="radio"]';
const ACTIVATES_ON_SPACE =
  'button,a[href],summary,input[type="checkbox"],input[type="radio"],[role="button"],[role="tab"],[role="menuitem"],[role="checkbox"],[role="switch"],[role="option"],[role="treeitem"],[role="row"]';

export type ReplayNotice = "unknown-playhead";

export type UseReplayOptions = {
  /** The run's events in canonical order (the run page's `events`). */
  events: readonly EventOut[];
  ctx: ReplayCtx;
  /** Every page of events has loaded: only then can an id in the URL be called unknown. */
  eventsComplete?: boolean;
  /** The inspector is open: Esc closes it first (it handles its own Esc), so replay's Esc does nothing. */
  inspectorOpen?: boolean;
  /** Start playing when replay is entered. Reduced motion always opens paused. */
  autoplay?: boolean;
  /** Attach the document keyboard map (default true). */
  keys?: boolean;
  checkpointEvery?: number;
};

type S = {
  mode: boolean;
  playing: boolean;
  speed: Speed;
  index: number;
  /** An event id from the URL not yet found in the loaded events. */
  wantedId: string | null;
  groupSteps: boolean;
  pauseAtFirstError: boolean;
  /** Playback paused itself at the first error; cleared by the next move. */
  pausedAtError: boolean;
  notice: ReplayNotice | null;
  announcement: string;
};

export type ReplayApi = {
  mode: boolean;
  playing: boolean;
  speed: Speed;
  /** The playhead (0 when the run has no events). */
  index: number;
  total: number;
  groupSteps: boolean;
  pauseAtFirstError: boolean;
  pausedAtError: boolean;
  notice: ReplayNotice | null;
  /** The projection at the playhead; null outside replay mode. */
  state: ReplayState | null;
  /** Recorded length of the whole run. */
  durationMs: number;
  /** "40 s model call, skipped to 1.5 s" for the gap just before the playhead's event, else null. */
  gapNote: string | null;
  /** Polite text for a screen reader; set on step, seek, enter and the first-error pause, never on a tick. */
  announcement: string;
  atStart: boolean;
  atEnd: boolean;
  enter: () => void;
  leave: () => void;
  toggleMode: () => void;
  play: () => void;
  pause: () => void;
  togglePlay: () => void;
  stepForward: () => void;
  stepBack: () => void;
  /** Move the playhead to an event index (clamped). Pauses playback. */
  seek: (index: number) => void;
  /** Move the playhead to an event id (the Show-it / jump-to-error paths). Returns whether the id is loaded. */
  seekToId: (id: string) => boolean;
  setSpeed: (speed: Speed) => void;
  setGroupSteps: (on: boolean) => void;
  setPauseAtFirstError: (on: boolean) => void;
  dismissNotice: () => void;
  /**
   * For an Esc handler that already owns the key (the timeline's, which calls preventDefault): close the inspector if it
   * is open, otherwise leave replay. Returns whether replay was left.
   */
  escape: () => boolean;
};

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

/** Typing places. A key pressed there is text, not a shortcut. */
export function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  return (
    t.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) ||
    t.getAttribute("role") === "textbox" ||
    t.getAttribute("role") === "combobox"
  );
}

function announceFor(events: readonly EventOut[], i: number): string {
  const e = events[i];
  if (!e) return "";
  const what = describe(e);
  return `Event ${i + 1} of ${events.length}, ${e.event_type}${what ? `, ${what}` : ""}`;
}

function writeUrl(id: string | null) {
  try {
    const q = new URLSearchParams(window.location.search);
    if (id) q.set("t", id);
    else q.delete("t");
    const qs = q.toString();
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${qs ? `?${qs}` : ""}${window.location.hash}`,
    );
  } catch {
    // a browser that refuses the write (rate limit, sandbox) only loses the shareable link
  }
}

export function useReplay(opts: UseReplayOptions): ReplayApi {
  const { events, ctx, eventsComplete = true, inspectorOpen = false, autoplay = false } = opts;
  const keys = opts.keys ?? true;
  const params = useSearchParams();
  const tParam = params?.get("t") || null;
  const n = events.length;

  const [s, setS] = useState<S>(() => ({
    mode: tParam != null,
    playing: false,
    speed: 1,
    index: 0,
    wantedId: tParam,
    groupSteps: true,
    pauseAtFirstError: true,
    pausedAtError: false,
    notice: null,
    announcement: "",
  }));

  // The URL changing under us (back/forward, a pasted link in the same page): adopt it, unless it is our own write.
  const [url, setUrl] = useState({ seen: tParam, written: tParam });
  if (url.seen !== tParam) {
    setUrl({ seen: tParam, written: url.written });
    if (tParam !== url.written) {
      setS((p) =>
        tParam == null
          ? { ...p, mode: false, playing: false, wantedId: null, pausedAtError: false }
          : {
              ...p,
              mode: true,
              playing: false,
              wantedId: tParam,
              pausedAtError: false,
              notice: null,
            },
      );
    }
  }

  const idIndex = useMemo(() => new Map(events.map((e, i) => [e.event_id, i])), [events]);
  const found = s.wantedId == null ? undefined : idIndex.get(s.wantedId);
  if (s.wantedId != null && s.mode && (found !== undefined || eventsComplete)) {
    const wanted = s.wantedId;
    setS((p) =>
      p.wantedId !== wanted
        ? p
        : {
            ...p,
            wantedId: null,
            index: found ?? 0,
            notice: found === undefined ? "unknown-playhead" : null,
            announcement: found === undefined ? "" : announceFor(events, found),
          },
    );
  }
  const index = n === 0 ? 0 : clamp(s.wantedId != null ? (found ?? 0) : s.index, 0, n - 1);

  // ctx's fields, not its identity: a caller building the object inline must not rebuild the projection each render.
  const { status } = ctx;
  const firstErrorId = ctx.firstErrorId ?? null;
  const checkpointEvery = opts.checkpointEvery;
  const projection = useMemo(
    () => new ReplayProjection(events, { status, firstErrorId }, { checkpointEvery }),
    [events, status, firstErrorId, checkpointEvery],
  );
  const pacer = useMemo(() => new ReplayPacer(events), [events]);
  const stops: readonly Stop[] = useMemo(
    () => groupStops(events, s.groupSteps),
    [events, s.groupSteps],
  );
  const state = useMemo(
    () => (s.mode ? projection.advance(index) : null),
    [s.mode, projection, index],
  );
  const firstErrorIndex = projection.firstErrorIndex;
  const durationMs = projection.durationMs;

  // ---- the URL: trailing-debounced writes; only the latest wins ----
  const writeTimer = useRef<number | null>(null);
  const flushUrl = useCallback((id: string | null, immediate = false) => {
    if (writeTimer.current != null) window.clearTimeout(writeTimer.current);
    const go = () => {
      writeTimer.current = null;
      writeUrl(id);
      setUrl((u) => ({ ...u, written: id }));
    };
    if (immediate) go();
    else writeTimer.current = window.setTimeout(go, URL_DEBOUNCE_MS);
  }, []);
  useEffect(
    () => () => {
      // Unmounting is navigating away: never write into the next page's URL.
      if (writeTimer.current != null) window.clearTimeout(writeTimer.current);
    },
    [],
  );

  // Latest values for handlers that run outside render (timers, key events).
  const live = useRef({ s, index, n, events, stops, firstErrorIndex });
  useEffect(() => {
    live.current = { s, index, n, events, stops, firstErrorIndex };
  });

  const idAt = (i: number) => live.current.events[i]?.event_id ?? null;

  const moveTo = useCallback(
    (to: number, announce = true) => {
      const { n: total, events: evs } = live.current;
      if (total === 0) return;
      const i = clamp(Math.floor(to), 0, total - 1);
      setS((p) => ({
        ...p,
        index: i,
        wantedId: null,
        playing: false,
        pausedAtError: false,
        notice: null,
        announcement: announce ? announceFor(evs, i) : p.announcement,
      }));
      flushUrl(evs[i]?.event_id ?? null);
    },
    [flushUrl],
  );

  const enter = useCallback(() => {
    const { n: total, events: evs, s: cur } = live.current;
    if (cur.mode) return;
    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    setS((p) => ({
      ...p,
      mode: true,
      index: 0,
      wantedId: null,
      playing: autoplay && !reduced && total > 1,
      pausedAtError: false,
      notice: null,
      announcement: announceFor(evs, 0),
    }));
    flushUrl(evs[0]?.event_id ?? null, true);
  }, [autoplay, flushUrl]);

  const leave = useCallback(() => {
    if (!live.current.s.mode) return;
    setS((p) => ({
      ...p,
      mode: false,
      playing: false,
      wantedId: null,
      pausedAtError: false,
      notice: null,
    }));
    flushUrl(null, true);
  }, [flushUrl]);

  const pause = useCallback(() => {
    const { s: cur, index: at } = live.current;
    if (!cur.playing) return;
    setS((p) => ({ ...p, playing: false }));
    flushUrl(idAt(at));
  }, [flushUrl]);

  const play = useCallback(() => {
    const { s: cur, n: total, index: at } = live.current;
    if (!cur.mode || total === 0) return;
    // Playing from the end starts over; a play press is never a no-op.
    setS((p) => ({
      ...p,
      playing: true,
      pausedAtError: false,
      notice: null,
      index: at >= total - 1 ? 0 : at,
      wantedId: null,
    }));
  }, []);

  const togglePlay = useCallback(() => {
    if (live.current.s.playing) pause();
    else play();
  }, [pause, play]);

  const stepFwd = useCallback(() => {
    const { index: at, stops: st } = live.current;
    moveTo(stepForward(st, at));
  }, [moveTo]);
  const stepBck = useCallback(() => {
    const { index: at, stops: st } = live.current;
    moveTo(stepBack(st, at));
  }, [moveTo]);

  const seekToId = useCallback(
    (id: string) => {
      const i = live.current.events.findIndex((e) => e.event_id === id);
      if (i < 0) return false;
      if (!live.current.s.mode) {
        setS((p) => ({ ...p, mode: true }));
      }
      moveTo(i);
      return true;
    },
    [moveTo],
  );

  const setSpeed = useCallback((speed: Speed) => setS((p) => ({ ...p, speed })), []);
  const bumpSpeed = useCallback((dir: 1 | -1) => {
    setS((p) => {
      const at = SPEEDS.indexOf(p.speed);
      return { ...p, speed: SPEEDS[clamp(at + dir, 0, SPEEDS.length - 1)]! };
    });
  }, []);

  const escape = useCallback((): boolean => {
    if (!live.current.s.mode || inspectorOpen) return false;
    leave();
    return true;
  }, [inspectorOpen, leave]);

  // ---- playback: one chained timer per tick ----
  useEffect(() => {
    if (!s.mode || !s.playing) return;
    if (index >= n - 1) return; // the end; the transition below turns `playing` off
    const id = window.setTimeout(
      () => {
        const next = index + 1;
        const atFirstError = shouldPauseAt(live.current, next);
        const atEnd = next >= live.current.n - 1;
        setS((p) => ({
          ...p,
          index: next,
          playing: !(atFirstError || atEnd),
          pausedAtError: atFirstError,
          announcement: atFirstError ? "Paused at the first error" : p.announcement,
        }));
        if (atFirstError || atEnd) flushUrl(live.current.events[next]?.event_id ?? null);
      },
      pacer.delayBefore(index + 1, s.speed),
    );
    return () => window.clearTimeout(id);
  }, [s.mode, s.playing, s.speed, index, n, pacer, flushUrl]);
  // Playing at the very end (a one-event run, or events that stopped growing) stops itself.
  const stuckAtEnd = s.mode && s.playing && n > 0 && index >= n - 1;
  if (stuckAtEnd) setS((p) => (p.playing ? { ...p, playing: false } : p));

  // ---- a hidden tab pauses: a background timer would play the run to its end unseen ----
  useEffect(() => {
    if (!s.mode) return;
    const onHide = () => {
      if (document.hidden) pause();
    };
    document.addEventListener("visibilitychange", onHide);
    return () => document.removeEventListener("visibilitychange", onHide);
  }, [s.mode, pause]);

  // ---- keyboard ----
  useEffect(() => {
    if (!keys) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
      if (isTypingTarget(e.target)) return;
      const mode = live.current.s.mode;
      if (e.key === "r") {
        if (e.repeat) return;
        e.preventDefault();
        if (mode) leave();
        else enter();
        return;
      }
      if (!mode || !MODE_KEYS.has(e.key)) return;
      const el = e.target instanceof HTMLElement ? e.target : null;
      if (e.key === " " && el?.closest(ACTIVATES_ON_SPACE)) return; // Space operates the focused control
      if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && el?.closest(OWN_KEYS)) return;
      switch (e.key) {
        case " ":
          if (e.repeat) return;
          togglePlay();
          break;
        case "ArrowLeft":
          stepBck();
          break;
        case "ArrowRight":
          stepFwd();
          break;
        case "[":
          bumpSpeed(-1);
          break;
        case "]":
          bumpSpeed(1);
          break;
        case "Escape":
          if (inspectorOpen || e.repeat) return;
          leave();
          break;
      }
      e.preventDefault();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [keys, inspectorOpen, enter, leave, togglePlay, stepBck, stepFwd, bumpSpeed]);

  return {
    mode: s.mode,
    playing: s.playing,
    speed: s.speed,
    index,
    total: n,
    groupSteps: s.groupSteps,
    pauseAtFirstError: s.pauseAtFirstError,
    pausedAtError: s.pausedAtError,
    notice: s.notice,
    state,
    durationMs,
    gapNote: s.mode ? pacer.gapNote(index) : null,
    announcement: s.announcement,
    atStart: index <= 0,
    atEnd: n === 0 || index >= n - 1,
    enter,
    leave,
    toggleMode: () => (live.current.s.mode ? leave() : enter()),
    play,
    pause,
    togglePlay,
    stepForward: stepFwd,
    stepBack: stepBck,
    seek: moveTo,
    seekToId,
    setSpeed,
    setGroupSteps: (on) => setS((p) => ({ ...p, groupSteps: on })),
    setPauseAtFirstError: (on) => setS((p) => ({ ...p, pauseAtFirstError: on })),
    dismissNotice: () => setS((p) => ({ ...p, notice: null })),
    escape,
  };
}

/** Whether arriving at `next` should stop playback because it is the first error and the person asked for that. */
function shouldPauseAt(cur: { s: S; firstErrorIndex: number }, next: number): boolean {
  return cur.s.pauseAtFirstError && cur.firstErrorIndex >= 0 && next === cur.firstErrorIndex;
}
