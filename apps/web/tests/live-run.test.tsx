import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "@/components/RunDetail";
import type { EventOut } from "@/lib/api/types";
import { runningRun, successRun } from "@/fixtures/runs";
import { fixtureReply } from "@/fixtures/api";
import { BASE, stubApi } from "./helpers";

/** A browser EventSource we can drive: open, deliver frames, drop, fail. */
class FakeEventSource {
  static all: FakeEventSource[] = [];
  readyState = 0;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  closed = false;
  private listeners = new Map<string, ((ev: MessageEvent) => void)[]>();
  constructor(readonly url: string) {
    FakeEventSource.all.push(this);
  }
  addEventListener(type: string, listener: (ev: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  open() {
    this.readyState = 1;
    act(() => this.onopen?.(new Event("open")));
  }
  trace(event: EventOut) {
    act(() => {
      for (const l of this.listeners.get("trace_event") ?? [])
        l({ data: JSON.stringify(event) } as MessageEvent);
    });
  }
  end() {
    act(() => {
      for (const l of this.listeners.get("run_end") ?? [])
        l({ data: '{"reason":"run_finished"}' } as MessageEvent);
    });
  }
  drop() {
    this.readyState = 0;
    act(() => this.onerror?.(new Event("error")));
  }
}

const sources = () => FakeEventSource.all;
const newest = () => sources().at(-1)!;

/** A copy of a fixture event that arrives later, optionally out of order. */
function later(base: EventOut, n: number, over: Partial<EventOut> = {}): EventOut {
  const at = new Date(Date.parse(base.occurred_at) + n * 1000).toISOString();
  return {
    ...base,
    event_id: `evt_LIVE${String(n).padStart(6, "0")}`,
    occurred_at: at,
    received_at: at,
    sequence: (base.sequence ?? 0) + n,
    status: null,
    duration_ms: null,
    payload: null,
    has_payload: false,
    ...over,
  };
}

function renderLive(runId: string, props: { live?: boolean } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const view = render(
    <QueryClientProvider client={client}>
      <RunDetail runId={runId} base={BASE} {...props} />
    </QueryClientProvider>,
  );
  return { client, ...view };
}

const types = () =>
  screen
    .queryAllByRole("option")
    .map((o) => o.querySelector(".tl-type")?.textContent)
    .filter(Boolean);

beforeEach(() => {
  FakeEventSource.all = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});
afterEach(() => vi.unstubAllGlobals());

async function running() {
  const calls = stubApi();
  const { run, events } = runningRun();
  const view = renderLive(run.id);
  await waitFor(() => expect(sources()).toHaveLength(1));
  return { calls, run, events, view };
}

/** Serves the fixture API, except the run record reports whatever event_count `grow` says. */
function stubGrowingRun(runId: string, grow: { count: number }) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string) => {
      const url = new URL(typeof input === "string" ? input : input.url);
      calls.push(url.pathname + url.search);
      const segs = url.pathname.replace(/^\/api\/abb\//, "").split("/");
      const r = fixtureReply(segs, url.searchParams);
      if (url.pathname.endsWith(`/v1/runs/${runId}`)) {
        const body = r.body as { summary: Record<string, unknown> };
        body.summary = { ...body.summary, event_count: grow.count };
      }
      return Response.json(r.body, { status: r.status });
    }),
  );
  return calls;
}

describe("live run detail", () => {
  it("opens the stream only after the history is loaded, resuming after the newest event", async () => {
    const { run, events } = await running();
    expect(newest().url).toBe(
      `/api/abb/v1/runs/${run.id}/stream?last_event_id=${events.at(-1)!.event_id}`,
    );
    expect(screen.getByTestId("progress")).toHaveTextContent(`${events.length} of`);
  });

  it("shows the connection and what the agent is doing, and appends events without a refresh", async () => {
    const { events } = await running();
    expect(screen.getByTestId("live-status")).toHaveTextContent("Connecting");
    newest().open();
    expect(screen.getByTestId("live-status")).toHaveTextContent("Live");
    const before = types().length;
    newest().trace(
      later(events.at(-1)!, 1, {
        event_type: "tool.call.started",
        attributes: { "tool.name": "open_pr" },
      }),
    );
    await waitFor(() => expect(types()).toHaveLength(before + 1));
    expect(types().at(-1)).toContain("tool.call.started");
    expect(screen.getByTestId("live-status")).toHaveTextContent("Running tool open_pr");
  });

  it("ignores duplicate delivery, including the resume overlap of events it already has", async () => {
    const { events } = await running();
    newest().open();
    const fresh = later(events.at(-1)!, 1);
    newest().trace(events.at(-1)!); // the server's overlap repeats history
    newest().trace(events[0]!);
    newest().trace(fresh);
    newest().trace(fresh);
    await waitFor(() => expect(types()).toHaveLength(events.length + 1));
    await new Promise((r) => setTimeout(r, 50));
    expect(types()).toHaveLength(events.length + 1);
  });

  it("puts an out-of-order event at its canonical position", async () => {
    const { events } = await running();
    newest().open();
    const newer = later(events.at(-1)!, 5);
    const between = later(events.at(-1)!, 2, { event_type: "retry.attempted" });
    newest().trace(newer);
    newest().trace(between); // arrives second, sorts first
    await waitFor(() => expect(types()).toHaveLength(events.length + 2));
    const tail = types().slice(-2);
    expect(tail[0]).toContain("retry.attempted");
    expect(tail[1]).toContain(newer.event_type);
  });

  it("says when its view may be incomplete, and recovers after reconnecting", async () => {
    await running();
    newest().open();
    newest().drop();
    const bar = screen.getByTestId("live-status");
    expect(bar).toHaveTextContent("Reconnecting");
    expect(screen.getByTestId("partial-data")).toHaveTextContent("Partial data");
    newest().open();
    expect(screen.queryByTestId("partial-data")).toBeNull();
    expect(screen.getByTestId("live-status")).toHaveTextContent("Live");
  });

  it("reloads the history after a long gap in live delivery", async () => {
    const calls = stubApi();
    const { run } = runningRun();
    const dateNow = vi.spyOn(Date, "now");
    renderLive(run.id);
    await waitFor(() => expect(sources()).toHaveLength(1));
    const eventLists = () => calls.filter((c) => c.includes("/events?")).length;
    newest().open();
    const before = eventLists();
    dateNow.mockReturnValue(1_000_000);
    newest().drop();
    dateNow.mockReturnValue(1_000_000 + 30_000);
    newest().open();
    await waitFor(() => expect(eventLists()).toBeGreaterThan(before));
    dateNow.mockRestore();
  });

  it("does not reload for a short blip", async () => {
    const calls = stubApi();
    const { run } = runningRun();
    const dateNow = vi.spyOn(Date, "now");
    renderLive(run.id);
    await waitFor(() => expect(sources()).toHaveLength(1));
    const eventLists = () => calls.filter((c) => c.includes("/events?")).length;
    newest().open();
    const before = eventLists();
    dateNow.mockReturnValue(1_000_000);
    newest().drop();
    dateNow.mockReturnValue(1_000_000 + 2_000);
    newest().open();
    await new Promise((r) => setTimeout(r, 100));
    expect(eventLists()).toBe(before);
    dateNow.mockRestore();
  });

  it("does not refetch the events list when the run grows while streaming", async () => {
    const calls = stubApi();
    const { run } = runningRun();
    const { client } = renderLive(run.id);
    await waitFor(() => expect(sources()).toHaveLength(1));
    newest().open();
    const eventLists = () => calls.filter((c) => c.includes("/events?")).length;
    const before = eventLists();
    await act(() => client.invalidateQueries({ queryKey: ["run", run.id] }));
    await new Promise((r) => setTimeout(r, 100));
    expect(eventLists()).toBe(before);
  });

  it("at run end, reloads the run and stops showing the live bar", async () => {
    const calls = stubApi();
    const { run } = runningRun();
    renderLive(run.id);
    await waitFor(() => expect(sources()).toHaveLength(1));
    newest().open();
    const runFetches = () => calls.filter((c) => c.endsWith(`/v1/runs/${run.id}`)).length;
    const eventFetches = () => calls.filter((c) => c.includes("/events?")).length;
    const before = [runFetches(), eventFetches()];
    newest().end();
    await waitFor(() => expect(runFetches()).toBeGreaterThan(before[0]!));
    await waitFor(() => expect(eventFetches()).toBeGreaterThan(before[1]!)); // final reconciliation
    expect(newest().closed).toBe(true);
    expect(screen.queryByTestId("live-status")).toBeNull();
    await new Promise((r) => setTimeout(r, 50));
    expect(sources()).toHaveLength(1); // and it does not reconnect
  });

  it("a browser refresh mid-run starts a new stream from the history it reloads", async () => {
    const { run, events, view } = await running();
    newest().open();
    view.unmount();
    expect(sources()[0]!.closed).toBe(true);
    renderLive(run.id);
    await waitFor(() => expect(sources()).toHaveLength(2));
    expect(newest().url).toContain(`last_event_id=${events.at(-1)!.event_id}`);
  });

  it("never opens a stream for a finished run", async () => {
    stubApi();
    const { run } = successRun();
    renderLive(run.id);
    await screen.findByTestId("headline");
    await waitFor(() => expect(screen.getByTestId("progress")).toHaveTextContent("events shown"));
    expect(sources()).toHaveLength(0);
    expect(screen.queryByTestId("live-status")).toBeNull();
  });

  it("does not stream when streaming is off (fixture data) and keeps polling instead", async () => {
    const calls = stubApi();
    const { run } = runningRun();
    const { client } = renderLive(run.id, { live: false });
    await screen.findByTestId("headline");
    await waitFor(() => expect(screen.getByTestId("progress")).toHaveTextContent("events shown"));
    expect(sources()).toHaveLength(0);
    expect(screen.queryByTestId("live-status")).toBeNull();
    // the run record is the only signal, as before: it still triggers an events reload
    const eventLists = () => calls.filter((c) => c.includes("/events?")).length;
    const before = eventLists();
    await act(() => client.invalidateQueries({ queryKey: ["run", run.id] }));
    expect(eventLists()).toBe(before); // unchanged record: nothing to reload
  });

  it("falls back to polling when live updates are unavailable", async () => {
    vi.unstubAllGlobals(); // no EventSource at all
    stubApi();
    const { run } = runningRun();
    renderLive(run.id);
    const bar = await screen.findByTestId("live-status");
    expect(within(bar).getByText(/unavailable/i)).toBeInTheDocument();
    expect(screen.getByTestId("partial-data")).toBeInTheDocument();
  });

  it("waits for a multi-page history to finish before it starts streaming", async () => {
    const { run, events } = runningRun();
    const half = Math.ceil(events.length / 2);
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request | string) => {
        const url = new URL(typeof input === "string" ? input : input.url);
        const segs = url.pathname.replace(/^\/api\/abb\//, "").split("/");
        if (url.pathname.endsWith(`/v1/runs/${run.id}/events`)) {
          const second = url.searchParams.has("cursor");
          if (second) await gate;
          return Response.json({
            items: second ? events.slice(half) : events.slice(0, half),
            next_cursor: second ? null : "page-2",
            ordering_mode: "sequence",
          });
        }
        const r = fixtureReply(segs, url.searchParams);
        return Response.json(r.body, { status: r.status });
      }),
    );
    renderLive(run.id);
    await screen.findByTestId("headline");
    await waitFor(() => expect(screen.getAllByRole("option").length).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 100));
    expect(sources()).toHaveLength(0); // the first page is on screen, but the history is incomplete
    release();
    await waitFor(() => expect(sources()).toHaveLength(1));
    expect(newest().url).toContain(`last_event_id=${events.at(-1)!.event_id}`);
  });

  it("while streaming, a growing run record does not trigger event reloads; without a stream it does", async () => {
    const { run } = runningRun();
    const grow = { count: 5 };
    const calls = stubGrowingRun(run.id, grow);
    const { client, unmount } = renderLive(run.id);
    await waitFor(() => expect(sources()).toHaveLength(1));
    newest().open();
    const eventLists = () => calls.filter((c) => c.includes("/events?")).length;
    const before = eventLists();
    grow.count = 9;
    await act(() => client.invalidateQueries({ queryKey: ["run", run.id] }));
    await new Promise((r) => setTimeout(r, 100));
    expect(eventLists()).toBe(before);
    unmount();

    // same growth with streaming off: the run record is the only signal, so the events reload
    const polled = stubGrowingRun(run.id, grow);
    const second = renderLive(run.id, { live: false });
    await waitFor(() => expect(screen.getByTestId("progress")).toHaveTextContent("events shown"));
    const start = polled.filter((c) => c.includes("/events?")).length;
    grow.count = 12;
    await act(() => second.client.invalidateQueries({ queryKey: ["run", run.id] }));
    await waitFor(() =>
      expect(polled.filter((c) => c.includes("/events?")).length).toBeGreaterThan(start),
    );
  });
});
