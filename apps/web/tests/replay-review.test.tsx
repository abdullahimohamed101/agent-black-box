import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "@/components/RunDetail";
import { Timeline } from "@/components/Timeline";
import { fixtureReply } from "@/fixtures/api";
import { wrongFixCodingRun } from "@/fixtures/codingRuns";
import type { FixtureRun } from "@/fixtures/builder";
import { failureRetryRun, stressRun } from "@/fixtures/runs";
import type { EventOut } from "@/lib/api/types";
import { formatCost } from "@/lib/format";
import { ReplayProjection, useReplay } from "@/lib/replay";
import { buildRows } from "@/lib/timeline";
import { BASE } from "./helpers";
import { ev } from "./spanEvents";

// Phase 12 review fixes that need a page or a hook: P1-1 and P1-2 as the person sees them, P2-1 (a live append is
// incremental), P2-2 (Esc with a hidden inspector), P3-1, P3-5 and P3-6.

const listeners = vi.hoisted(() => new Set<() => void>());
vi.mock("next/navigation", async () => {
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    listeners.add(cb);
    return () => listeners.delete(cb);
  };
  return {
    usePathname: () => "/w/demo/projects/all/runs/x",
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
beforeEach(() => {
  realReplace(null, "", "/w/demo/projects/all/runs/x");
  vi.spyOn(window.history, "replaceState").mockImplementation((s, t, u) => {
    realReplace(s, t, u);
    act(() => listeners.forEach((l) => l()));
  });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

type Edit = (events: EventOut[], url: URL) => EventOut[];
/** The fixture API as a given role sees it, optionally rewriting the events list the way a different server would. */
function stub(viewer: boolean, edit?: Edit) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === "string" ? input : input.url);
      const segs = url.pathname.replace(/^\/api\/abb\//, "").split("/");
      const r = fixtureReply(segs, url.searchParams, { content: !viewer });
      const body = r.body as { items?: EventOut[] };
      if (edit && segs.at(-1) === "events" && body.items)
        return Response.json({ ...body, items: edit(body.items, url) }, { status: r.status });
      return Response.json(r.body, { status: r.status });
    }),
  );
}

const figure = (label: string) => {
  const dl = document.querySelector('dl[aria-label="Run summary"]') as HTMLElement;
  return within(dl).getByText(label, { selector: "dt" }).closest(".stat")!.querySelector("dd")!;
};
const scrub = () => screen.getByRole("slider", { name: "Replay position" });
const seek = (i: number) => fireEvent.change(scrub(), { target: { value: String(i) } });

async function enter(f: FixtureRun) {
  const user = userEvent.setup();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  render(
    <QueryClientProvider client={client}>
      <RunDetail runId={f.run.id} base={BASE} live={false} />
    </QueryClientProvider>,
  );
  const button = await screen.findByRole("button", { name: "Replay" });
  await waitFor(() => expect(button).toBeEnabled());
  await waitFor(() =>
    expect(screen.getByTestId("progress")).toHaveTextContent(`${f.events.length} of`),
  );
  const live = { files: figure("Files changed").textContent, cost: figure("Cost").textContent };
  await user.click(button);
  await screen.findByRole("region", { name: "Replay controls" });
  return { user, button, live };
}

describe("P1-1 on the page: a viewer's Files changed", () => {
  const f = wrongFixCodingRun(); // two distinct files, one edit each
  const last = f.events.length - 1;
  const firstEdit = f.events.findIndex((e) => e.event_type === "file.modified");

  it("the owner's replay counts distinct files at every playhead, exactly", async () => {
    stub(false);
    const { live } = await enter(f);
    expect(live.files).toBe("2");
    seek(firstEdit);
    expect(figure("Files changed").textContent).toBe("1");
    seek(last);
    expect(figure("Files changed").textContent).toBe("2");
    expect(figure("Files changed").title).toBe("");
  });

  it("the viewer's replay agrees with the viewer's own header at the end, and says 'up to' before it", async () => {
    stub(true);
    const { live } = await enter(f);
    expect(live.files).toBe("2"); // the header, from the summary (it is metadata)
    seek(last);
    expect(figure("Files changed").textContent).toBe(live.files); // before the fix this said 1
    seek(firstEdit);
    const mid = figure("Files changed");
    expect(mid.textContent).toContain("up to 1");
    expect(mid.title).toMatch(/paths are withheld for your role/);
    expect(within(mid).getByText(/paths are withheld/)).toHaveClass("sr-only");
    // before any file event nothing is approximate yet
    seek(0);
    expect(figure("Files changed").textContent).toBe("0");
  });
});

describe("P1-2 on the page: Cost so far when the server priced the calls", () => {
  const f = failureRetryRun();
  const last = f.events.length - 1;
  const summary = f.run.summary["estimated_cost_usd"] as number;
  const noAttribute: Edit = (items) =>
    items.map((e) => {
      const { ["cost.estimated_usd"]: _x, ...rest } = e.attributes;
      void _x;
      return { ...e, attributes: rest };
    });

  it("a plain SDK run (no cost attribute) replays the header's figure at the last event, not $0.00", async () => {
    stub(false, noAttribute);
    const { live } = await enter(f);
    expect(live.cost).toBe(formatCost(summary));
    seek(last);
    expect(figure("Cost").textContent).toBe(live.cost);
    expect(figure("Cost").querySelector("small")).toBeNull();
    expect(live.cost).not.toBe("$0.00");
  });

  it("calls nothing could price make the figure a floor, and the page says how many", async () => {
    stub(false, (items) =>
      items.map((e, i) =>
        e.event_type === "llm.request.completed" && i % 2 === 0
          ? { ...e, cost_usd: 0, cost_unpriced: true }
          : e,
      ),
    );
    await enter(f);
    seek(last);
    const dd = figure("Cost");
    expect(dd.textContent).toMatch(/^at least \$/);
    expect(dd.querySelector("small")!.textContent).toMatch(/^\d+ calls? unpriced$/);
    // and the headline detail says it too
    expect(document.body.textContent).toMatch(/at least \$[\d.]+ so far · \d+ calls? unpriced/);
  });
});

describe("P2-2: Esc with the inspector hidden by the playhead", () => {
  const f = failureRetryRun();

  it("leaves replay in one press, and no 'Loading the event' notice hangs around", async () => {
    stub(false);
    const { user, button } = await enter(f);
    seek(f.events.length - 1);
    await user.click(screen.getAllByRole("option").at(-1)!);
    await screen.findByRole("dialog");
    // Play from the end starts over, and the follow only runs while paused: the event the inspector holds is now in the
    // future, so its dialog is not on screen
    await user.click(screen.getByRole("button", { name: "Play" }));
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByTestId("event-notice")).toBeNull();
    // Esc from the page must leave replay, not be swallowed by a dialog nobody can see
    (document.activeElement as HTMLElement | null)?.blur();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(button).toHaveAttribute("aria-pressed", "false"));
  });

  it("with the dialog really on screen Esc still closes it first", async () => {
    stub(false);
    const { user, button } = await enter(f);
    seek(f.events.length - 1);
    await user.click(screen.getAllByRole("option").at(-1)!);
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(button).toHaveAttribute("aria-pressed", "true");
  });
});

describe("P3-5: the playhead inside a collapsed group", () => {
  const events = failureRetryRun().events;
  const t0 = Math.min(...events.map((e) => Date.parse(e.occurred_at)));
  const renderTimeline = (collapsed: ReadonlySet<string>, currentId: string) =>
    render(
      <Timeline
        rows={buildRows(events, collapsed)}
        t0={t0}
        selectedKey={null}
        firstErrorId={null}
        faultStep={null}
        currentId={currentId}
        onSelect={() => {}}
        onActivate={() => {}}
        onEscape={() => {}}
        onJumpToError={() => {}}
      />,
    );

  it("marks the group row when it stands for the current event, and the event's own row when expanded", () => {
    const groups = buildRows(events, new Set()).filter((r) => r.kind === "group");
    const group = groups[0]!;
    if (group.kind !== "group") throw new Error("expected a group");
    const inside = group.eventIds.at(-1)!;
    const folded = renderTimeline(new Set([group.key]), inside);
    const current = screen.getAllByRole("option").filter((o) => o.getAttribute("aria-current"));
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveClass("tl-group");
    folded.unmount();
    renderTimeline(new Set(), inside);
    const open = screen.getAllByRole("option").filter((o) => o.getAttribute("aria-current"));
    expect(open).toHaveLength(1);
    expect(open[0]).not.toHaveClass("tl-group");
  });
});

describe("P3-5: a filter that hides the playhead's event says so", () => {
  it("adds a note to the progress line", async () => {
    const f = failureRetryRun();
    stub(false);
    const { user } = await enter(f);
    seek(0); // run.started: an event of the "run" class
    expect(screen.getByTestId("progress")).not.toHaveTextContent(/hidden by the filters/);
    await user.click(screen.getByRole("checkbox", { name: "Errors only" }));
    expect(screen.getByTestId("progress")).toHaveTextContent(
      "the event at the playhead is hidden by the filters",
    );
  });
});

// ---- hook-level ----
const hookEvents = (f: { events: EventOut[]; run: { status: string } }) => ({
  events: f.events,
  ctx: { status: f.run.status },
});

describe("P2-2/P3-1: hook", () => {
  it("r does nothing on a run with no events, like the disabled button", () => {
    const { result } = renderHook(() => useReplay({ events: [], ctx: { status: "RUNNING" } }));
    act(() => {
      document.body.dispatchEvent(
        new KeyboardEvent("keydown", { key: "r", bubbles: true, cancelable: true }),
      );
    });
    expect(result.current.mode).toBe(false);
    act(() => result.current.enter());
    expect(result.current.mode).toBe(false);
    expect(window.location.search).toBe("");
  });

  it("a function for inspectorOpen is read when Esc is pressed", () => {
    const f = failureRetryRun();
    let shown = true;
    const { result } = renderHook(() =>
      useReplay({ ...hookEvents(f), inspectorOpen: () => shown }),
    );
    act(() => result.current.enter());
    const esc = () =>
      act(() => {
        document.body.dispatchEvent(
          new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }),
        );
      });
    esc();
    expect(result.current.mode).toBe(true); // an inspector is on screen: it takes the key
    shown = false;
    esc();
    expect(result.current.mode).toBe(false);
  });
});

describe("P2-1: a live append is incremental", () => {
  const stress = stressRun();
  const spyApply = () =>
    vi.spyOn(ReplayProjection.prototype as unknown as { apply: (i: number) => void }, "apply");

  it("appending one event to a 10,000-event run does not re-walk the run", () => {
    const all = stress.events;
    const before = all.slice(0, all.length - 1);
    const apply = spyApply();
    const { result, rerender } = renderHook(
      (p: { events: EventOut[] }) =>
        useReplay({ events: p.events, ctx: { status: "RUNNING" }, eventsComplete: false }),
      { initialProps: { events: before } },
    );
    act(() => result.current.enter());
    act(() => result.current.seek(before.length - 2));
    const walked = apply.mock.calls.length;
    expect(walked).toBeGreaterThanOrEqual(before.length - 1); // the first walk to the playhead
    expect(result.current.state!.counts.events).toBe(before.length - 1);

    apply.mockClear();
    rerender({ events: all }); // a new array, one event longer: what useLiveEvents hands over
    expect(result.current.total).toBe(all.length);
    expect(result.current.index).toBe(before.length - 2); // the playhead did not move
    expect(result.current.state!.counts.events).toBe(before.length - 1);
    expect(apply).not.toHaveBeenCalled(); // before the fix: a whole new projection walked ~10,000 events again

    // moving onto the new event applies exactly that one
    act(() => result.current.seek(all.length - 1));
    expect(apply).toHaveBeenCalledTimes(2); // the one that was beyond the old playhead and the appended one
    expect(result.current.state!.counts.events).toBe(all.length);
  });

  it("a late event landing mid-run is not an append: the projection starts over and is still right", () => {
    const f = failureRetryRun();
    const sorted = f.events;
    const late = { ...sorted[3]!, event_id: "evt_late_arrival" } as EventOut;
    const withLate = [...sorted.slice(0, 3), late, ...sorted.slice(3)];
    const { result, rerender } = renderHook(
      (p: { events: EventOut[] }) => useReplay({ events: p.events, ctx: { status: f.run.status } }),
      { initialProps: { events: sorted } },
    );
    act(() => result.current.enter());
    act(() => result.current.seek(8));
    rerender({ events: withLate });
    expect(result.current.state!.events).toEqual(withLate.slice(0, 9));
    expect(result.current.state!.counts.events).toBe(9);
  });
});

describe("P3-6: playback keeps the wait it has done", () => {
  // three events with 10 s between them: every gap is clamped to 1.5 s at 1x, 300 ms at 5x
  const slow = [
    ev("run.started", 1, 0),
    ev("tool.call.completed", 2, 10),
    ev("run.completed", 3, 20),
  ];
  const run = (ms: number) => {
    for (let t = 0; t < ms; t += 10) act(() => void vi.advanceTimersByTime(10));
  };
  const start = (events: EventOut[]) => {
    vi.useFakeTimers();
    const hook = renderHook(
      (p: { events: EventOut[] }) =>
        useReplay({ events: p.events, ctx: { status: "RUNNING" }, eventsComplete: false }),
      { initialProps: { events } },
    );
    act(() => hook.result.current.enter());
    act(() => hook.result.current.play());
    return hook;
  };

  it("speeding up late in a gap waits only for the rest of it", () => {
    const { result } = start(slow);
    run(1200); // 80% of the 1.5 s gap
    expect(result.current.index).toBe(0);
    act(() => result.current.setSpeed(5)); // the whole gap would now be 300 ms; 20% of it is 60 ms
    run(100);
    expect(result.current.index).toBe(1); // before the fix it waited a further 300 ms
  });

  it("a live event arriving mid-gap does not restart the wait", () => {
    const grown = [...slow, ev("agent.completed", 4, 30)];
    const { result, rerender } = start(slow);
    run(1200);
    rerender({ events: grown });
    run(400);
    expect(result.current.index).toBe(1); // 1.2 s + 0.4 s > 1.5 s; before the fix the arrival restarted the 1.5 s
  });

  it("pausing and playing again at the same event starts the gap afresh", () => {
    const { result } = start(slow);
    run(1200);
    act(() => result.current.pause());
    run(2000);
    act(() => result.current.play());
    run(1400);
    expect(result.current.index).toBe(0);
    run(200);
    expect(result.current.index).toBe(1);
  });
});
