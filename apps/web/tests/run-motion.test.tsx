import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RunDetail } from "@/components/RunDetail";
import { RunHeadline } from "@/components/RunHeadline";
import { Timeline } from "@/components/Timeline";
import { withFindings } from "@/fixtures/findings";
import { failureRetryRun, failingChainRun, runningRun, successRun } from "@/fixtures/runs";
import { headlineModel } from "@/lib/runHeadline";
import { buildRows } from "@/lib/timeline";
import { BASE, renderWithQuery, stubApi } from "./helpers";

vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/projects/all/runs/x",
  useSearchParams: () => new URLSearchParams(""),
}));
afterEach(() => vi.unstubAllGlobals());

/** A window that has not asked for reduced motion (jsdom has no matchMedia, which RunDetail reads as "no motion"). */
function stubMotion() {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: /no-preference/.test(query),
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })),
  );
}

const readyModel = (f: ReturnType<typeof failureRetryRun>, status?: string) =>
  headlineModel({
    run: { ...f.run, ...(status ? { status: status as never } : {}) },
    events: f.events,
    t0: f.events.reduce((m, e) => Math.min(m, Date.parse(e.occurred_at)), Infinity),
    eventsComplete: true,
  });

describe("headline crossfade (spec 2)", () => {
  it("does not fade on first render, fades once per change of state, not on a re-render", () => {
    const f = failureRetryRun();
    const failed = readyModel(f);
    const recovered = readyModel(f, "SUCCESS");
    const { container, rerender } = render(
      <RunHeadline kind={failed.kind} variant="ready" model={failed} onGo={() => undefined} />,
    );
    expect(container.querySelector(".hl")).not.toHaveAttribute("data-fade");
    rerender(
      <RunHeadline kind={failed.kind} variant="ready" model={failed} onGo={() => undefined} />,
    );
    expect(container.querySelector(".hl")).not.toHaveAttribute("data-fade");
    rerender(
      <RunHeadline
        kind={recovered.kind}
        variant="ready"
        model={recovered}
        onGo={() => undefined}
      />,
    );
    expect(failed.kind).not.toBe(recovered.kind);
    expect(container.querySelector(".hl")).toHaveAttribute("data-fade");
  });

  it("a focused Go button keeps focus through a change of state, and the fade still replays (review P3-9)", () => {
    const f = failureRetryRun();
    const failed = readyModel(f);
    const recovered = readyModel(f, "SUCCESS");
    const { container, rerender } = render(
      <RunHeadline kind={failed.kind} variant="ready" model={failed} onGo={() => undefined} />,
    );
    const go = screen.getByRole("button", { name: "Show it" });
    go.focus();
    rerender(
      <RunHeadline
        kind={recovered.kind}
        variant="ready"
        model={recovered}
        onGo={() => undefined}
      />,
    );
    expect(screen.getByRole("button", { name: "Show it" })).toBe(go);
    expect(go).toHaveFocus();
    const first = container.querySelector(".hl")!.getAttribute("data-fade");
    rerender(
      <RunHeadline kind={failed.kind} variant="ready" model={failed} onGo={() => undefined} />,
    );
    // a second change flips the marker, which restarts the CSS animation without remounting
    expect(container.querySelector(".hl")!.getAttribute("data-fade")).not.toBe(first);
    expect(go).toHaveFocus();
  });

  it("a skeleton turning into the headline is a load, not a change of state", () => {
    const f = failureRetryRun();
    const m = readyModel(f);
    const { container, rerender } = render(<RunHeadline kind={m.kind} variant="skeleton" />);
    rerender(<RunHeadline kind={m.kind} variant="ready" model={m} onGo={() => undefined} />);
    expect(container.querySelector(".hl")).not.toHaveAttribute("data-fade");
  });
});

describe("the fault line's entrance (spec 1)", () => {
  it("is marked once when the first error is on screen, never again on a view switch", async () => {
    stubApi();
    const user = userEvent.setup();
    const { run } = failureRetryRun();
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    const work = container.querySelector(".work")!;
    await waitFor(() => expect(work).toHaveAttribute("data-fault-intro"));
    await waitFor(() => expect(work).not.toHaveAttribute("data-fault-intro"), { timeout: 2000 });
    await user.click(screen.getByRole("radio", { name: /Waterfall/ }));
    await user.click(screen.getByRole("radio", { name: /Timeline/ }));
    await new Promise((r) => setTimeout(r, 60));
    expect(work).not.toHaveAttribute("data-fault-intro");
  });

  it("a run without an error never marks it", async () => {
    stubApi();
    const { run } = successRun();
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    await new Promise((r) => setTimeout(r, 60));
    expect(container.querySelector(".work")).not.toHaveAttribute("data-fault-intro");
  });
});

describe("the jump outline (spec 3)", () => {
  it("is red for the first error (Go) and the accent for any other evidence", async () => {
    stubApi();
    const user = userEvent.setup();
    const { run } = withFindings(failingChainRun());
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    await user.click(await screen.findByRole("button", { name: "Show it" }));
    await waitFor(() => expect(container.querySelector('[data-landed="error"]')).not.toBeNull());
    await waitFor(() => expect(container.querySelector("[data-landed]")).toBeNull(), {
      timeout: 2000,
    });
    await user.click(screen.getAllByRole("button", { name: "Show evidence" })[0]!);
    await waitFor(() => expect(container.querySelector('[data-landed="evidence"]')).not.toBeNull());
  });
});

describe("the inspector's exit (spec 4)", () => {
  it("eases out for ~100 ms when motion is allowed, and is gone at once when it is not", async () => {
    stubApi();
    stubMotion();
    const user = userEvent.setup();
    const { run } = failureRetryRun();
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    await user.click(await screen.findByRole("button", { name: "Show it" }));
    const drawer = await screen.findByRole("dialog");
    expect(drawer).not.toHaveAttribute("data-closing");
    await user.click(screen.getByRole("button", { name: "Close details" }));
    expect(container.querySelector(".drawer")).toHaveAttribute("data-closing");
    expect(container.querySelector(".drawer")).toHaveAttribute("inert");
    expect(container.querySelector(".work")).toHaveAttribute("data-inspector", "open");
    await waitFor(() => expect(container.querySelector(".drawer")).toBeNull());
    expect(container.querySelector(".work")).toHaveAttribute("data-inspector", "closed");
  });

  it("without motion (reduced, or no matchMedia) it unmounts immediately", async () => {
    stubApi();
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      })),
    );
    const user = userEvent.setup();
    const { run } = failureRetryRun();
    const { container } = renderWithQuery(<RunDetail runId={run.id} base={BASE} />);
    await screen.findByRole("listbox", { name: /Run timeline/ });
    await user.click(await screen.findByRole("button", { name: "Show it" }));
    await screen.findByRole("dialog");
    await user.click(screen.getByRole("button", { name: "Close details" }));
    expect(container.querySelector(".drawer")).toBeNull();
  });
});

describe("live rows keep their place (spec 5)", () => {
  const run = runningRun();
  const rows = buildRows(run.events, new Set());
  const props = {
    rows,
    t0: Date.parse(run.events[0]!.occurred_at),
    selectedKey: null,
    firstErrorId: null,
    onSelect: () => undefined,
    onActivate: () => undefined,
    onEscape: () => undefined,
    onJumpToError: () => undefined,
  };
  const metrics = (scrollTop: number) => {
    const set = (k: string, v: number) =>
      Object.defineProperty(HTMLElement.prototype, k, { configurable: true, get: () => v });
    set("scrollHeight", 5000);
    set("clientHeight", 500);
    Object.defineProperty(HTMLElement.prototype, "scrollTop", {
      configurable: true,
      get: () => scrollTop,
      set: () => undefined,
    });
  };
  afterEach(() => {
    for (const k of ["scrollHeight", "clientHeight", "scrollTop"])
      delete (HTMLElement.prototype as unknown as Record<string, unknown>)[k];
  });

  it("a reader scrolled away gets 'N new events' and the list does not move; the control goes to the edge", async () => {
    metrics(0);
    const user = userEvent.setup();
    const { rerender } = render(<Timeline {...props} arrived={0} />);
    fireEvent.scroll(screen.getByRole("listbox"));
    rerender(<Timeline {...props} arrived={3} />);
    const control = await screen.findByTestId("new-events");
    expect(control).toHaveTextContent("3 new events");
    rerender(<Timeline {...props} arrived={4} />);
    expect(await screen.findByTestId("new-events")).toHaveTextContent("4 new events");
    await act(async () => {
      await user.click(screen.getByTestId("new-events"));
    });
    expect(screen.queryByTestId("new-events")).toBeNull();
  });

  it("a resize that moves the reader off the live edge is noticed without a scroll (review P3-3)", async () => {
    metrics(4500);
    let notify: () => void = () => undefined;
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(cb: () => void) {
          notify = cb;
        }
        observe() {}
        disconnect() {}
        unobserve() {}
      },
    );
    const { rerender } = render(<Timeline {...props} arrived={0} />);
    // the pane shrinks: 4500 + 500 was the edge, 4500 + 200 is not
    Object.defineProperty(HTMLElement.prototype, "clientHeight", {
      configurable: true,
      get: () => 200,
    });
    act(() => notify());
    rerender(<Timeline {...props} arrived={2} />);
    expect(await screen.findByTestId("new-events")).toHaveTextContent("2 new events");
    vi.unstubAllGlobals();
  });

  it("a reader at the live edge gets no control (the list follows)", async () => {
    metrics(4500);
    const { rerender } = render(<Timeline {...props} arrived={0} />);
    fireEvent.scroll(screen.getByRole("listbox"));
    rerender(<Timeline {...props} arrived={2} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("new-events")).toBeNull();
  });
});
