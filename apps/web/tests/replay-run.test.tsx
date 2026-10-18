import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "@/components/RunDetail";
import { fixtureReply } from "@/fixtures/api";
import { baselineCodingRun } from "@/fixtures/codingRuns";
import type { FixtureRun } from "@/fixtures/builder";
import { failureRetryRun, multiAgentRun, runningRun, successRun } from "@/fixtures/runs";
import type { EventOut } from "@/lib/api/types";
import { formatCost, formatDuration, formatInt } from "@/lib/format";
import { WITHHELD_TEXT } from "@/lib/withheld";
import { BASE, renderWithQuery, stubApi } from "./helpers";

// The run page in replay (Phase 12 step 3). next/navigation is backed by the real URL, as in replay-hook.test.tsx, so
// `?t=` and `?view=` round trip through location and history like in the browser.
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

const failed = failureRetryRun();
const errIdx = failed.events.findIndex((e) => e.event_id === failed.run.first_error!.event_id);

/** Figures computed here from the raw events, independently of the projection under test. */
function expected(f: FixtureRun, i: number) {
  const so = f.events.slice(0, i + 1);
  const cost = so.reduce((s, e) => {
    const c = e.attributes["cost.estimated_usd"];
    return s + (typeof c === "number" ? c : 0);
  }, 0);
  const bad = (e: EventOut) =>
    e.status === "error" || e.status === "timeout" || e.event_type.endsWith(".failed");
  return {
    cost: formatCost(Number(cost.toFixed(6))),
    retries: so.filter((e) => e.event_type === "retry.attempted").length,
    errors: so.filter(bad).length,
    modelCalls: so.filter((e) => /^llm\.request\.(completed|failed)$/.test(e.event_type)).length,
    toolCalls: so.filter((e) => /^tool\.call\.(completed|failed)$/.test(e.event_type)).length,
    files: new Set(
      so
        .filter((e) => /^file\.(created|modified|deleted)$/.test(e.event_type))
        .map((e) => e.attributes["file.path"]),
    ).size,
  };
}

/** A figure of the run: one of the five readouts, or one that moved into Details (model calls, tool calls, files changed). */
const figureCell = (label: string) => {
  const dt = [
    ...document.querySelectorAll('dl[aria-label="Run summary"] dt, #run-details dt'),
  ].find((d) => d.textContent === label);
  if (!dt) throw new Error(`no readout named ${label}`);
  return dt.parentElement!.querySelector("dd")!;
};
const figure = (label: string) => figureCell(label).textContent;
const scrub = () => screen.getByRole("slider", { name: "Replay position" });
const seek = (i: number) => fireEvent.change(scrub(), { target: { value: String(i) } });
const timelineTypes = () =>
  screen
    .queryAllByRole("option")
    .map((o) => o.querySelector(".tl-type")?.textContent)
    .filter(Boolean);

async function enter(f: FixtureRun = failed) {
  const user = userEvent.setup();
  renderWithQuery(<RunDetail runId={f.run.id} base={BASE} live={false} />);
  const button = await screen.findByRole("button", { name: "Replay" });
  await waitFor(() => expect(button).toBeEnabled());
  await waitFor(() =>
    expect(screen.getByTestId("progress")).toHaveTextContent(`${f.events.length} of`),
  );
  const live = {
    cost: figure("Cost"),
    retries: figure("Retries"),
    errors: figure("Errors"),
    duration: figure("Duration"),
  };
  await user.click(button);
  await screen.findByRole("region", { name: "Replay controls" });
  return { user, button, live };
}

describe("replay mode on the run page", () => {
  it("has a Replay button with a shortcut hint that toggles the mode and docks the transport", async () => {
    stubApi();
    const { user, button } = await enter();
    expect(button).toHaveAttribute("aria-pressed", "true");
    expect(button).toHaveAttribute("aria-keyshortcuts", "r");
    expect(button).toHaveTextContent("Replay");
    expect(button).toHaveAttribute("title", "Replay this run (r)"); // the hint is a tooltip now, not a drawn key
    // paused at the first event; the transport sits under the header, above the work area
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
    const bar = screen.getByRole("region", { name: "Replay controls" });
    const header = document.querySelector(".run-head")!;
    const work = document.querySelector(".work")!;
    expect(header.compareDocumentPosition(bar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(bar.compareDocumentPosition(work) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await user.click(button);
    expect(button).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("region", { name: "Replay controls" })).toBeNull();
  });

  it("the header shows the figures as of the playhead, and the live ones again after leaving", async () => {
    stubApi();
    const { user, live } = await enter();
    // (re-read after leaving: the live readouts must come back exactly)
    for (const i of [0, 3, errIdx, errIdx + 2, failed.events.length - 1]) {
      seek(i);
      const x = expected(failed, i);
      expect(figure("Cost"), `cost at ${i}`).toBe(x.cost);
      expect(figure("Retries"), `retries at ${i}`).toBe(formatInt(x.retries));
      expect(figure("Errors"), `errors at ${i}`).toBe(formatInt(x.errors));
      expect(figure("Model calls"), `model calls at ${i}`).toBe(formatInt(x.modelCalls));
      expect(figure("Tool calls"), `tool calls at ${i}`).toBe(formatInt(x.toolCalls));
      expect(figure("Files changed"), `files at ${i}`).toBe(formatInt(x.files));
      expect(screen.getByTestId("replay-status")).toHaveTextContent("as of this moment");
    }
    // at the last event the replay equals the run summary
    expect(figure("Cost")).toBe(live.cost);
    expect(figure("Retries")).toBe(live.retries);
    expect(figure("Errors")).toBe(live.errors);
    expect(figure("Elapsed")).toBe(formatDuration(failed.run.duration_ms));
    await user.click(screen.getByRole("button", { name: "Leave replay" }));
    expect(figure("Duration")).toBe(live.duration);
    expect(screen.queryByTestId("replay-status")).toBeNull();
  });

  it("the headline does not leak the future: no failure text before the first error is reached", async () => {
    stubApi();
    await enter();
    const headline = () => screen.getByTestId("headline");
    const detail = () => screen.getByTestId("headline-detail");
    for (const i of [0, 2, errIdx - 1]) {
      seek(i);
      expect(headline()).toHaveTextContent(/^Running at step \d+, replay\.$/);
      expect(screen.queryByTestId("first-error")).toBeNull();
      expect(screen.queryByRole("button", { name: "Go to first error" })).toBeNull();
      const text = `${headline().textContent} ${detail().textContent} ${screen.getByTestId("replay-status").textContent}`;
      expect(text).not.toMatch(/run_migration|ConnectionTimeout|Failed|First error|Succeeded/);
    }
    seek(errIdx);
    const err = screen.getByTestId("first-error");
    expect(err).toHaveTextContent(
      /^First error at step \d+ after 6\.2 s: run_migration, ConnectionTimeout/,
    );
    expect(within(err).getByRole("button", { name: "Go to first error" })).toBeInTheDocument();
    expect(detail().textContent).toContain(`${expected(failed, errIdx).cost} so far`);
    // and the outcome (the live "Failed at step") is never stated in replay
    seek(failed.events.length - 1);
    expect(headline()).not.toHaveTextContent(/^Failed at step/);
  });

  it("the timeline grows with the playhead and the fault line appears when the first error is reached", async () => {
    stubApi();
    await enter();
    seek(0);
    expect(timelineTypes()).toEqual([failed.events[0]!.event_type]);
    expect(document.querySelector(".tl-fault")).toBeNull();
    seek(errIdx - 1);
    expect(timelineTypes()).toEqual(failed.events.slice(0, errIdx).map((e) => e.event_type));
    expect(document.querySelector(".tl-fault")).toBeNull();
    expect(timelineTypes()).not.toContain("retry.attempted");
    seek(errIdx);
    expect(document.querySelector(".tl-fault")).not.toBeNull();
    expect(timelineTypes().at(-1)).toBe(failed.events[errIdx]!.event_type);
    // the row at the playhead is the current one
    const rows = screen.getAllByRole("option");
    expect(rows.filter((r) => r.getAttribute("aria-current") === "step")).toHaveLength(1);
    expect(rows.at(-1)).toHaveAttribute("aria-current", "step");
    expect(screen.getByTestId("progress")).toHaveTextContent(
      `${errIdx + 1} of ${failed.events.length} events shown, replay`,
    );
    // stepping back hides the fault line again
    seek(errIdx - 2);
    expect(document.querySelector(".tl-fault")).toBeNull();
  });

  it("the inspector follows the playhead only when it is open, and never shows a later event", async () => {
    stubApi();
    const { user } = await enter();
    seek(3);
    expect(screen.queryByRole("dialog")).toBeNull(); // replay does not force it open
    await user.click(screen.getAllByRole("option").at(-1)!);
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Step forward" }));
    await waitFor(() =>
      expect(screen.getByRole("dialog")).toHaveTextContent(
        new RegExp(failed.events[4]!.event_type.replace(/\./g, "\\.")),
      ),
    );
    // the drawer never shows an event beyond the playhead
    seek(0);
    await waitFor(() =>
      expect(screen.getByRole("dialog")).toHaveTextContent(
        new RegExp(failed.events[0]!.event_type.replace(/\./g, "\\.")),
      ),
    );
  });

  it("Go to first error moves the playhead to the first error and opens it", async () => {
    stubApi();
    const { user } = await enter();
    seek(errIdx);
    await user.click(screen.getByRole("button", { name: "Go to first error" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("ConnectionTimeout");
    expect(scrub()).toHaveValue(String(errIdx));
  });

  it("the Findings tab says its totals reflect the whole run; Evidence shows the whole run with a note", async () => {
    stubApi();
    const { user } = await enter(baselineCodingRun());
    await user.click(screen.getByRole("tab", { name: "Findings" }));
    expect(screen.getByTestId("replay-context-note")).toHaveTextContent(
      "Findings and agent totals reflect the whole run.",
    );
    await user.click(screen.getByRole("tab", { name: "Evidence" }));
    expect(screen.getByTestId("replay-evidence-note")).toHaveTextContent(
      "Evidence shows the whole run.",
    );
    await user.click(screen.getByRole("tab", { name: "Timeline" }));
    expect(screen.queryByTestId("replay-evidence-note")).toBeNull();
  });

  it("the coding story shows only the steps that have happened", async () => {
    const f = baselineCodingRun();
    stubApi();
    await enter(f);
    const steps = () => within(screen.getByTestId("story")).getAllByRole("listitem").length;
    seek(1);
    const early = screen.queryByTestId("story") ? steps() : 0;
    seek(f.events.length - 1);
    const all = steps();
    expect(all).toBeGreaterThan(early);
    seek(Math.floor(f.events.length / 2));
    expect(steps()).toBeLessThan(all);
  });
});

describe("replay and the waterfall", () => {
  const multi = multiAgentRun();

  it("spans appear as the playhead reaches them, open ones are in progress, and the agent tree follows", async () => {
    stubApi();
    const { user } = await enter(multi);
    await user.click(screen.getByRole("tab", { name: "Waterfall" }));
    const spanCount = () =>
      Number(
        /(\d[\d,]*) spans/.exec(document.querySelector(".wf-count")?.textContent ?? "0 spans")![1],
      );
    seek(0);
    const first = spanCount();
    seek(Math.floor(multi.events.length / 2));
    const mid = spanCount();
    seek(multi.events.length - 1);
    const all = spanCount();
    expect(first).toBeLessThan(mid);
    expect(mid).toBeLessThanOrEqual(all);
    expect(all).toBe(new Set(multi.events.map((e) => e.span_id).filter(Boolean)).size);
    // an open span reads "in progress", never "no end recorded"
    seek(Math.floor(multi.events.length / 2));
    const grid = screen.getByRole("treegrid");
    expect(grid.textContent).toMatch(/in progress/);
    expect(grid.textContent).not.toMatch(/no end recorded/);
    // the row at the playhead is current
    expect(
      within(grid)
        .getAllByRole("row")
        .filter((r) => r.getAttribute("aria-current") === "step"),
    ).toHaveLength(1);
    // the agent tree (Steps column) highlights the agent that emitted the current event
    const at = Math.floor(multi.events.length / 2);
    seek(at);
    const tree = screen.getByTestId("agent-tree");
    const current = within(tree)
      .getAllByRole("button")
      .filter((b) => b.getAttribute("aria-current") === "true");
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent(multi.events[at]!.agent_id.slice(0, 20));
  });

  it("the skew note shows on a run whose clocks disagree", async () => {
    stubApi();
    await enter(multi);
    expect(screen.getByRole("region", { name: "Replay controls" })).toHaveTextContent(
      "Timing across agents is approximate.",
    );
  });

  it("a single-agent run with sane clocks has no skew note", async () => {
    stubApi();
    await enter();
    expect(screen.getByRole("region", { name: "Replay controls" })).not.toHaveTextContent(
      "approximate",
    );
  });

  it("the fault line on the waterfall appears when the first error is reached", async () => {
    stubApi();
    const f = failed;
    const { user } = await enter(f);
    await user.click(screen.getByRole("tab", { name: "Waterfall" }));
    seek(errIdx - 1);
    expect(document.querySelector(".wf-fault-line")).toBeNull();
    seek(errIdx);
    expect(document.querySelector(".wf-fault-line")).not.toBeNull();
  });
});

describe("replay as a viewer", () => {
  it("is metadata only, withheld panels stay, and replaying fetches nothing", async () => {
    const f = baselineCodingRun();
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request | string) => {
        const url = new URL(typeof input === "string" ? input : input.url);
        calls.push(url.pathname + url.search);
        const segs = url.pathname.replace(/^\/api\/abb\//, "").split("/");
        const r = fixtureReply(segs, url.searchParams, { content: false });
        return Response.json(r.body, { status: r.status });
      }),
    );
    const { user } = await enter(f);
    const before = calls.length;
    // play the whole run by seeking, step, and change the speed: all from the loaded events
    seek(5);
    await user.click(screen.getByRole("button", { name: "Step forward" }));
    await user.click(screen.getByRole("button", { name: "5x" }));
    seek(f.events.length - 1);
    expect(calls.length).toBe(before);
    // no captured content anywhere on the page
    const secrets = f.events.flatMap((e) =>
      ["shell.command", "file.path"].flatMap((k) => {
        const v = e.attributes[k];
        return typeof v === "string" && v.length >= 3 ? [v] : [];
      }),
    );
    const surface = document.body.textContent ?? "";
    for (const s of secrets) expect(surface, `leaked ${s}`).not.toContain(s);
    // opening an event shows the withheld wording (an event row, not a group: a group row only folds)
    const eventRows = screen
      .getAllByRole("option")
      .filter((o) => !/^Group /.test(o.getAttribute("aria-label") ?? ""));
    await user.click(eventRows.at(-1)!);
    await screen.findByRole("dialog");
    await waitFor(() => expect(document.body.textContent).toContain(WITHHELD_TEXT));
  });

  it("entering and playing replay from loaded data issues no request", async () => {
    const calls = stubApi();
    await enter();
    await waitFor(() => expect(screen.getByTestId("progress")).toHaveTextContent("of"));
    const before = calls.length;
    for (const i of [2, errIdx, failed.events.length - 1, 0]) seek(i);
    expect(calls.length).toBe(before);
  });
});

describe("keyboard and URL", () => {
  it("r toggles replay, Space plays and pauses, Esc closes the inspector first and then leaves", async () => {
    stubApi();
    const { user, button } = await enter();
    button.blur(); // Space on a focused button operates that button, so shortcuts are tried from the page
    await user.keyboard("r");
    expect(button).toHaveAttribute("aria-pressed", "false");
    await user.keyboard("r");
    expect(button).toHaveAttribute("aria-pressed", "true");
    await user.keyboard(" ");
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    await user.keyboard(" ");
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
    // Esc with the inspector open closes the inspector only
    await user.click(screen.getAllByRole("option").at(-1)!);
    await screen.findByRole("dialog");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(button).toHaveAttribute("aria-pressed", "true");
    // then Esc leaves replay
    await user.keyboard("{Escape}");
    await waitFor(() => expect(button).toHaveAttribute("aria-pressed", "false"));
  });

  it("Space on the timeline plays instead of opening a row; j and k still move the selection", async () => {
    stubApi();
    const { user } = await enter();
    seek(3);
    const list = screen.getByRole("listbox", { name: /Run timeline/ });
    list.focus();
    await user.keyboard("j");
    expect(list.getAttribute("aria-activedescendant")).toBeTruthy();
    await user.keyboard(" ");
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("Space on the Play button toggles once (no double fire)", async () => {
    stubApi();
    const { user } = await enter();
    screen.getByRole("button", { name: "Play" }).focus();
    await user.keyboard(" ");
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    await user.keyboard(" ");
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
  });

  it("arrow keys on the scrub bar are left to the slider, not also used to step", async () => {
    stubApi();
    await enter();
    seek(4);
    // jsdom does not move a range on ArrowRight; what matters is that replay's own map does not claim the key.
    const notClaimed = fireEvent.keyDown(scrub(), { key: "ArrowRight" });
    expect(notClaimed).toBe(true);
    expect(scrub()).toHaveValue("4");
    // the same key anywhere else steps
    document.body.focus();
    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    expect(
      Number(scrub().getAttribute("aria-valuenow") ?? (scrub() as HTMLInputElement).value),
    ).toBeGreaterThan(4);
  });

  it("typing in an input is not a shortcut", async () => {
    stubApi();
    const { user, button } = await enter();
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    await user.keyboard("r ");
    expect(button).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
    input.remove();
  });

  it("the playhead is in the URL, ?view= coexists, and leaving removes t", async () => {
    stubApi();
    const { user } = await enter();
    seek(4);
    await waitFor(
      () => expect(window.location.search).toContain(`t=${failed.events[4]!.event_id}`),
      {
        timeout: 2000,
      },
    );
    await user.click(screen.getByRole("tab", { name: "Waterfall" }));
    const q = new URLSearchParams(window.location.search);
    expect(q.get("view")).toBe("waterfall");
    expect(q.get("t")).toBe(failed.events[4]!.event_id);
    await user.click(screen.getByRole("button", { name: "Leave replay" }));
    const after = new URLSearchParams(window.location.search);
    expect(after.get("t")).toBeNull();
    expect(after.get("view")).toBe("waterfall");
  });

  it("focus returns to the Replay button when the transport's Leave button is used", async () => {
    stubApi();
    const { user, button } = await enter();
    await user.click(screen.getByRole("button", { name: "Leave replay" }));
    await waitFor(() => expect(button).toHaveFocus());
  });
});

describe("deep links", () => {
  it("?t=<event id> opens replay at that event", async () => {
    realReplace(null, "", `/w/demo/projects/all/runs/x?t=${failed.events[6]!.event_id}`);
    stubApi();
    renderWithQuery(<RunDetail runId={failed.run.id} base={BASE} live={false} />);
    await screen.findByRole("region", { name: "Replay controls" });
    await waitFor(() => expect(scrub()).toHaveValue("6"));
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument(); // paused
    expect(screen.getByRole("button", { name: "Replay" })).toHaveAttribute("aria-pressed", "true");
    expect(timelineTypes()).toHaveLength(7);
  });

  it("an unknown id degrades to the start with a notice", async () => {
    realReplace(null, "", "/w/demo/projects/all/runs/x?t=evt_does_not_exist");
    stubApi();
    renderWithQuery(<RunDetail runId={failed.run.id} base={BASE} live={false} />);
    expect(await screen.findByText(/That moment is not in this run/)).toBeInTheDocument();
    expect(scrub()).toHaveValue("0");
  });
});

describe("live runs", () => {
  class FakeEventSource {
    static all: FakeEventSource[] = [];
    readyState = 0;
    onopen: ((ev: Event) => void) | null = null;
    onerror: ((ev: Event) => void) | null = null;
    private listeners = new Map<string, ((ev: MessageEvent) => void)[]>();
    constructor(readonly url: string) {
      FakeEventSource.all.push(this);
    }
    addEventListener(type: string, l: (ev: MessageEvent) => void) {
      this.listeners.set(type, [...(this.listeners.get(type) ?? []), l]);
    }
    close() {
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
  }

  it("keeps the playhead where it is while events arrive, replays only what was received", async () => {
    FakeEventSource.all = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    stubApi();
    const f = runningRun();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={client}>
        <RunDetail runId={f.run.id} base={BASE} />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(FakeEventSource.all).toHaveLength(1));
    const es = FakeEventSource.all[0]!;
    es.open();
    await user.click(await screen.findByRole("button", { name: "Replay" }));
    seek(2);
    expect(scrub()).toHaveValue("2");
    const total = f.events.length;
    expect(scrub().getAttribute("aria-valuetext")).toMatch(new RegExp(`^event 3 of ${total},`));
    const last = f.events.at(-1)!;
    const at = (n: number) => new Date(Date.parse(last.occurred_at) + n * 1000).toISOString();
    for (const n of [1, 2]) {
      es.trace({
        ...last,
        event_id: `evt_LIVE${n}`,
        occurred_at: at(n),
        received_at: at(n),
        sequence: (last.sequence ?? 0) + n,
        status: null,
        duration_ms: null,
        payload: null,
        has_payload: false,
      });
    }
    await waitFor(() =>
      expect(scrub().getAttribute("aria-valuetext")).toMatch(
        new RegExp(`^event 3 of ${total + 2},`),
      ),
    );
    expect(scrub()).toHaveValue("2");
    expect(timelineTypes()).toHaveLength(3); // the two new events are beyond the playhead
    await user.click(screen.getByRole("button", { name: "Leave replay" }));
    await waitFor(() => expect(timelineTypes()).toHaveLength(total + 2));
  });

  it("a finished run's success fixture replays to its summary", async () => {
    stubApi();
    const f = successRun();
    await enter(f);
    seek(f.events.length - 1);
    expect(figure("Cost")).toBe(formatCost(f.run.summary["estimated_cost_usd"] as number));
    expect(screen.getByTestId("headline")).toHaveTextContent(/^Completed at step \d+, replay\.$/);
  });
});
