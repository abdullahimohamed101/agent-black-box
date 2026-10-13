import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { failureRetryRun, successRun } from "@/fixtures/runs";
import { ReplayPacer, useReplay, type ReplayApi, type UseReplayOptions } from "@/lib/replay";

// next/navigation is mocked with the URL itself as the store: `useSearchParams` reads `location.search` and
// `history.replaceState` (what the hook writes with, as the run page does for ?view=) notifies the subscribers, so the
// round trip through the URL is real and a test can also change it "from outside" like back/forward does.
const listeners = vi.hoisted(() => new Set<() => void>());
vi.mock("next/navigation", async () => {
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    listeners.add(cb);
    return () => listeners.delete(cb);
  };
  return {
    useSearchParams: () => {
      const q = React.useSyncExternalStore(
        subscribe,
        () => window.location.search,
        () => window.location.search,
      );
      return new URLSearchParams(q);
    },
  };
});

const realReplace = window.history.replaceState.bind(window.history);
let writes: string[] = [];
const outside = (url: string) => {
  realReplace(null, "", url);
  act(() => listeners.forEach((l) => l()));
};

beforeEach(() => {
  vi.useFakeTimers();
  writes = [];
  realReplace(null, "", "/runs/x");
  vi.spyOn(window.history, "replaceState").mockImplementation((s, t, u) => {
    writes.push(String(u));
    realReplace(s, t, u);
    listeners.forEach((l) => l());
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
});

const failed = failureRetryRun();
const ok = successRun();
const ctxOf = (f: typeof failed) => ({
  status: f.run.status,
  firstErrorId: f.run.first_error?.event_id ?? null,
});
const firstErrIdx = failed.events.findIndex((e) => e.event_id === failed.run.first_error!.event_id);

function setup(over: Partial<UseReplayOptions> = {}, f = failed) {
  return renderHook((p: Partial<UseReplayOptions>) =>
    useReplay({ events: f.events, ctx: ctxOf(f), ...over, ...p }),
  );
}
const tick = (ms: number) => act(() => void vi.advanceTimersByTime(ms));
/** Long stretches of playback in small steps: each tick re-renders and schedules the next, which needs a commit between. */
const run = (ms: number) => {
  for (let t = 0; t < ms; t += 25) tick(25);
};
const press = (key: string, target: EventTarget = document.body, init: KeyboardEventInit = {}) =>
  act(() => {
    target.dispatchEvent(
      new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init }),
    );
  });
const search = () => new URLSearchParams(window.location.search);
type R = { current: ReplayApi };
const enterAndPlay = (r: R) => {
  act(() => r.current.enter());
  act(() => r.current.play());
};

describe("useReplay: mode, playback and pacing", () => {
  it("starts off with no projection, and entering opens paused at the first event", () => {
    const { result } = setup();
    expect(result.current).toMatchObject({ mode: false, playing: false, index: 0, state: null });
    act(() => result.current.enter());
    expect(result.current).toMatchObject({ mode: true, playing: false, index: 0 });
    expect(result.current.state!.counts.events).toBe(1);
    expect(search().get("t")).toBe(failed.events[0]!.event_id);
  });

  it("plays one event per paced delay, and a speed divides the delay", () => {
    const pacer = new ReplayPacer(failed.events);
    const { result } = setup();
    enterAndPlay(result);
    expect(result.current.playing).toBe(true);
    const d1 = pacer.delayBefore(1, 1);
    tick(d1 - 1);
    expect(result.current.index).toBe(0);
    tick(1);
    expect(result.current.index).toBe(1);
    act(() => result.current.setSpeed(5));
    tick(pacer.delayBefore(2, 5) - 1);
    expect(result.current.index).toBe(1);
    tick(1);
    expect(result.current.index).toBe(2);
    act(() => result.current.setSpeed(2));
    tick(pacer.delayBefore(3, 2));
    expect(result.current.index).toBe(3);
    expect(result.current.state!.current).toBe(failed.events[3]);
  });

  it("pauses at the first error, says so, and the state there is the independent computation", () => {
    const { result } = setup();
    enterAndPlay(result);
    run(30_000);
    expect(result.current.index).toBe(firstErrIdx);
    expect(result.current.playing).toBe(false);
    expect(result.current.pausedAtError).toBe(true);
    expect(result.current.announcement).toBe("Paused at the first error");
    expect(result.current.state!.firstErrorReached).toBe(true);
    const so = failed.events.slice(0, firstErrIdx + 1);
    expect(result.current.state!.counts.errors).toBe(
      so.filter((e) => e.status === "error" || e.event_type.endsWith(".failed")).length,
    );
    expect(search().get("t")).toBe(failed.run.first_error!.event_id);
    // playing again carries on past the error
    act(() => result.current.play());
    expect(result.current.pausedAtError).toBe(false);
    tick(1500);
    expect(result.current.index).toBeGreaterThan(firstErrIdx);
  });

  it("with the option off it plays through the error and stops at the end", () => {
    const { result } = setup();
    enterAndPlay(result);
    act(() => result.current.setPauseAtFirstError(false));
    run(60_000);
    expect(result.current.index).toBe(failed.events.length - 1);
    expect(result.current.playing).toBe(false);
    expect(result.current.atEnd).toBe(true);
    expect(vi.getTimerCount()).toBeLessThanOrEqual(1); // at most the debounced URL write
    // play at the end starts over
    act(() => result.current.play());
    expect(result.current).toMatchObject({ index: 0, playing: true });
  });

  it("a one-event run cannot play", () => {
    const { result } = setup({ events: failed.events.slice(0, 1) });
    enterAndPlay(result);
    expect(result.current.playing).toBe(false);
  });

  it("steps by stop (a request's started and completed are one) or by single event", () => {
    const { result } = setup({ events: ok.events, ctx: ctxOf(ok) });
    act(() => result.current.enter());
    const start = ok.events.findIndex((e) => e.event_type === "llm.request.started");
    act(() => result.current.seek(start - 1));
    act(() => result.current.stepForward());
    expect(result.current.index).toBe(start + 1);
    expect(result.current.announcement).toMatch(
      new RegExp(`^Event ${start + 2} of ${ok.events.length}, llm.request.completed`),
    );
    act(() => result.current.stepBack());
    expect(result.current.index).toBe(start - 1);
    act(() => result.current.setGroupSteps(false));
    act(() => result.current.seek(start - 1));
    act(() => result.current.stepForward());
    expect(result.current.index).toBe(start);
    act(() => result.current.stepBack());
    act(() => result.current.stepBack());
    act(() => result.current.stepBack());
    expect(result.current.index).toBe(start - 3);
  });

  it("seek clamps, pauses, and leaves the projection at that event", () => {
    const { result } = setup();
    enterAndPlay(result);
    act(() => result.current.seek(5));
    expect(result.current).toMatchObject({ index: 5, playing: false });
    act(() => result.current.seek(10_000));
    expect(result.current.index).toBe(failed.events.length - 1);
    act(() => result.current.seek(-3));
    expect(result.current.index).toBe(0);
    let ok = false;
    act(() => void (ok = result.current.seekToId(failed.events[4]!.event_id)));
    expect(ok).toBe(true);
    expect(result.current.index).toBe(4);
    act(() => void (ok = result.current.seekToId("evt_nope")));
    expect(ok).toBe(false);
  });

  it("a hidden tab pauses playback", () => {
    const { result } = setup();
    enterAndPlay(result);
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    act(() => void document.dispatchEvent(new Event("visibilitychange")));
    expect(result.current.playing).toBe(false);
    expect(vi.getTimerCount()).toBeLessThanOrEqual(1);
  });

  it("unmounting clears every timer", () => {
    const { result, unmount } = setup();
    enterAndPlay(result);
    act(() => result.current.seek(3));
    act(() => result.current.play());
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("reduced motion opens paused even when autoplay is asked for; otherwise autoplay plays", () => {
    const mm = (reduce: boolean) =>
      vi.stubGlobal("matchMedia", (q: string) => ({
        matches: reduce && /reduce/.test(q),
        media: q,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      }));
    mm(true);
    const a = setup({ autoplay: true });
    act(() => a.result.current.enter());
    expect(a.result.current.playing).toBe(false);
    act(() => a.result.current.play());
    expect(a.result.current.playing).toBe(true); // user action still plays
    a.unmount();
    realReplace(null, "", "/runs/x");
    mm(false);
    const b = setup({ autoplay: true });
    act(() => b.result.current.enter());
    expect(b.result.current.playing).toBe(true);
  });
});

describe("useReplay: the URL", () => {
  it("writes on pause, seek, step and leave, and never on a tick", () => {
    const { result } = setup();
    enterAndPlay(result);
    const afterEnter = writes.length;
    run(3000);
    expect(result.current.index).toBeGreaterThan(1);
    expect(writes).toHaveLength(afterEnter); // ticks wrote nothing
    act(() => result.current.pause());
    tick(200);
    expect(search().get("t")).toBe(failed.events[result.current.index]!.event_id);
    act(() => result.current.seek(7));
    tick(200);
    expect(search().get("t")).toBe(failed.events[7]!.event_id);
    act(() => result.current.stepBack());
    tick(200);
    expect(search().get("t")).toBe(failed.events[result.current.index]!.event_id);
    act(() => result.current.leave());
    expect(search().has("t")).toBe(false);
    expect(window.location.pathname).toBe("/runs/x");
  });

  it("debounces a dragged scrub bar to one write and keeps the other query parameters", () => {
    realReplace(null, "", "/runs/x?view=waterfall");
    const { result } = setup();
    act(() => result.current.enter());
    writes = [];
    for (const i of [2, 3, 4, 5, 6]) act(() => result.current.seek(i));
    tick(200);
    expect(writes).toHaveLength(1);
    expect(search().get("view")).toBe("waterfall");
    expect(search().get("t")).toBe(failed.events[6]!.event_id);
  });

  it("opens in replay at the event named by ?t= (a reload), paused", () => {
    realReplace(null, "", `/runs/x?t=${failed.events[6]!.event_id}`);
    const { result } = setup();
    expect(result.current).toMatchObject({ mode: true, playing: false, index: 6, notice: null });
    expect(result.current.state!.current).toBe(failed.events[6]);
  });

  it("an unknown id degrades to the start with a notice, once every event has loaded", () => {
    realReplace(null, "", "/runs/x?t=evt_gone");
    const { result, rerender } = setup({
      eventsComplete: false,
      events: failed.events.slice(0, 3),
    });
    expect(result.current.notice).toBeNull(); // still paging: the id may be on a later page
    rerender({ eventsComplete: true });
    expect(result.current).toMatchObject({ mode: true, index: 0, notice: "unknown-playhead" });
    act(() => result.current.dismissNotice());
    expect(result.current.notice).toBeNull();
  });

  it("an id on a page that loads later is found when it arrives", () => {
    const id = failed.events[8]!.event_id;
    realReplace(null, "", `/runs/x?t=${id}`);
    const { result, rerender } = setup({
      eventsComplete: false,
      events: failed.events.slice(0, 3),
    });
    expect(result.current.notice).toBeNull();
    rerender({ events: failed.events, eventsComplete: true });
    expect(result.current).toMatchObject({ index: 8, notice: null });
  });

  it("follows back/forward: another id moves the playhead paused, no id leaves replay, and its own writes are not echoed", () => {
    const { result } = setup();
    act(() => result.current.enter());
    act(() => result.current.seek(4));
    tick(200);
    expect(result.current.index).toBe(4);
    outside(`/runs/x?t=${failed.events[9]!.event_id}`);
    expect(result.current).toMatchObject({ mode: true, index: 9, playing: false });
    outside("/runs/x");
    expect(result.current.mode).toBe(false);
    outside(`/runs/x?t=${failed.events[2]!.event_id}`);
    expect(result.current).toMatchObject({ mode: true, index: 2 });
  });

  it("a live run: events that only append leave the playhead where it is", () => {
    const { result, rerender } = setup({
      events: failed.events.slice(0, 10),
      eventsComplete: false,
    });
    act(() => result.current.enter());
    act(() => result.current.seek(6));
    rerender({ events: failed.events, eventsComplete: true });
    expect(result.current.index).toBe(6);
    expect(result.current.total).toBe(failed.events.length);
    expect(result.current.state!.counts.events).toBe(7);
  });
});

describe("useReplay: keyboard", () => {
  it("Space plays and pauses, arrows step, [ and ] change speed, r and Esc enter and leave", () => {
    const { result } = setup();
    press("r");
    expect(result.current.mode).toBe(true);
    press(" ");
    expect(result.current.playing).toBe(true);
    press(" ");
    expect(result.current.playing).toBe(false);
    const i = result.current.index;
    press("ArrowRight");
    expect(result.current.index).toBeGreaterThan(i);
    press("ArrowLeft");
    expect(result.current.index).toBe(i);
    press("]");
    expect(result.current.speed).toBe(2);
    press("]");
    press("]");
    expect(result.current.speed).toBe(5);
    press("[");
    expect(result.current.speed).toBe(2);
    press("[");
    press("[");
    expect(result.current.speed).toBe(1);
    press("Escape");
    expect(result.current.mode).toBe(false);
    press("r");
    expect(result.current.mode).toBe(true);
    press("r");
    expect(result.current.mode).toBe(false);
  });

  it("only r does anything outside replay mode", () => {
    const { result } = setup();
    for (const k of [" ", "ArrowRight", "ArrowLeft", "[", "]", "Escape"]) press(k);
    expect(result.current).toMatchObject({ mode: false, index: 0, speed: 1 });
  });

  it("does nothing while typing, with a modifier held, or when another handler took the key", () => {
    const { result } = setup();
    act(() => result.current.enter());
    for (const tag of ["input", "textarea", "select"]) {
      const el = document.body.appendChild(document.createElement(tag));
      for (const k of ["r", " ", "ArrowRight", "]", "Escape"]) press(k, el);
    }
    const editable = document.body.appendChild(document.createElement("div"));
    Object.defineProperty(editable, "isContentEditable", { value: true });
    press("ArrowRight", editable);
    expect(result.current).toMatchObject({ mode: true, index: 0, speed: 1, playing: false });
    for (const m of ["ctrlKey", "metaKey", "altKey", "shiftKey"] as const) {
      for (const k of [" ", "ArrowRight", "]", "r", "Escape"])
        press(k, document.body, { [m]: true });
    }
    expect(result.current).toMatchObject({ mode: true, index: 0, speed: 1, playing: false });
    // the timeline's own handler took Space: replay leaves it alone
    const el = document.body.appendChild(document.createElement("div"));
    el.addEventListener("keydown", (e) => e.preventDefault());
    press(" ", el);
    expect(result.current.playing).toBe(false);
  });

  it("Space on a focused button operates the button, and arrows inside a tab list or slider stay theirs", () => {
    const { result } = setup();
    act(() => result.current.enter());
    const btn = document.body.appendChild(document.createElement("button"));
    press(" ", btn);
    expect(result.current.playing).toBe(false);
    const tabs = document.body.appendChild(document.createElement("div"));
    tabs.setAttribute("role", "tablist");
    const tab = tabs.appendChild(document.createElement("span"));
    press("ArrowRight", tab);
    expect(result.current.index).toBe(0);
  });

  it("Esc closes the inspector first: with it open, replay's Esc does nothing; escape() reports what it did", () => {
    const { result, rerender } = setup({ inspectorOpen: true });
    act(() => result.current.enter());
    press("Escape");
    expect(result.current.mode).toBe(true);
    let left = true;
    act(() => void (left = result.current.escape()));
    expect(left).toBe(false);
    rerender({ inspectorOpen: false });
    press("Escape");
    expect(result.current.mode).toBe(false);
    act(() => result.current.enter());
    act(() => void (left = result.current.escape()));
    expect(left).toBe(true);
    expect(result.current.mode).toBe(false);
  });

  it("keys: false attaches no listener", () => {
    const { result } = setup({ keys: false });
    press("r");
    expect(result.current.mode).toBe(false);
  });
});
