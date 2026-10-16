import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_FILTERS, RunsList, type ListFilters } from "@/components/RunsList";
import { SetupGuide } from "@/components/SetupGuide";
import { Empty, ErrorState } from "@/components/States";
import { FaultCut, RecorderTape, TraceLine } from "@/components/illustrations";
import ErrorPage from "@/app/error";
import NotFound from "@/app/not-found";
import { fixtureRuns } from "@/fixtures";
import { ApiRequestError } from "@/lib/api/client";
import { BASE, renderWithQuery, stubApi } from "./helpers";

// Phase 22 step 8 (D9): the setup-guide empty state and the small illustration system.
afterEach(() => vi.unstubAllGlobals());

const none = () => Response.json({ items: [], next_cursor: null });

function Harness({ initial = DEFAULT_FILTERS }: { initial?: ListFilters }) {
  const [f, setF] = useState<ListFilters>(initial);
  return (
    <RunsList base={BASE} filters={f} onFilters={setF} now={Date.parse("2026-10-08T00:00:00Z")} />
  );
}

describe("illustrations", () => {
  it.each([
    ["trace line", TraceLine],
    ["fault-line cut", FaultCut],
    ["recorder tape", RecorderTape],
  ])("%s is decoration: aria-hidden, unfocusable, no colour literals", (_n, Cmp) => {
    const { container } = render(<Cmp />);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("aria-hidden")).toBe("true");
    expect(svg.getAttribute("focusable")).toBe("false");
    expect(svg.querySelector("title")).toBeNull();
    // Two colours only, both tokens: currentColor and the accent class (set in illustrations.css).
    expect(svg.outerHTML).not.toMatch(/#[0-9a-f]{3,6}|rgb\(/i);
    expect(svg.outerHTML.length).toBeLessThan(2500);
    expect(svg.querySelector(".illus-accent")).not.toBeNull();
  });
});

describe("Empty and ErrorState carry a motif", () => {
  it("Empty shows the sentence, the motif and the action", () => {
    render(
      <Empty title="No evaluations yet" motif="tape" action={<button type="button">Start</button>}>
        Nobody has evaluated this run.
      </Empty>,
    );
    const el = document.querySelector('[data-state="empty"]')!;
    expect(el.querySelector("svg.illus")).not.toBeNull();
    expect(within(el as HTMLElement).getByRole("button", { name: "Start" })).toBeInTheDocument();
    expect(el).toHaveTextContent("No evaluations yet");
  });

  it("a not-found keeps its motif and a routine error does not (ADR-166: drawings only on first-run and not-found); both keep their words", () => {
    const { rerender } = render(<ErrorState error={new Error("boom")} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load this data");
    expect(screen.getByRole("alert").querySelector("svg.illus")).toBeNull();
    rerender(
      <ErrorState error={new ApiRequestError("no such run", 404, "NOT_FOUND", null, false)} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Not found");
    expect(screen.getByRole("alert").querySelector("svg.illus")).not.toBeNull();
  });

  it("the error boundary offers a retry and never prints the error message", async () => {
    const reset = vi.fn();
    const err = Object.assign(new Error("secret db password in here"), { digest: "d123" });
    render(<ErrorPage error={err} reset={reset} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Something broke");
    expect(document.body.textContent).not.toMatch(/secret db password/);
    expect(document.body.textContent).toMatch(/d123/);
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledOnce();
  });

  it("the error boundary's alert is the sentence, not the whole page with its links (review P3-12)", () => {
    render(<ErrorPage error={Object.assign(new Error("x"), { digest: "d1" })} reset={() => {}} />);
    const alert = screen.getByRole("alert");
    expect(alert.tagName).toBe("P");
    expect(alert).toHaveTextContent(/Nothing you recorded was lost/);
    expect(alert.querySelector("a, button")).toBeNull();
    expect(document.querySelector("main")).not.toHaveAttribute("role");
  });

  it("the 404 page has one h1, a way home and a motif", () => {
    render(<NotFound />);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "Go to your runs" })).toHaveAttribute("href", "/");
    expect(document.querySelector("svg.illus")).not.toBeNull();
  });
});

describe("SetupGuide", () => {
  it("shows three steps, the real package and snippet, and that the token is shown once", () => {
    render(<SetupGuide base={BASE} />);
    expect(screen.getByRole("heading", { level: 2, name: "No runs yet" })).toBeInTheDocument();
    const steps = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(steps).toEqual(["Create an API key", "Install the SDK", "Record a run"]);
    expect(document.body.textContent).toMatch(/shown only once/);
    expect(document.body.textContent).toContain("pip install agent-black-box");
    expect(document.body.textContent).toContain("from blackbox import BlackBox");
    expect(screen.getByRole("link", { name: /API keys|Create a key/ })).toHaveAttribute(
      "href",
      "/w/demo/settings/api-keys",
    );
    expect(screen.getByTestId("setup-waiting")).toHaveTextContent("Waiting for your first event");
  });

  it("copy buttons have an accessible name and announce Copied politely", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    // userEvent.setup() installs its own clipboard stub; replace its writeText after setup.
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<SetupGuide base={BASE} />);
    const btn = screen.getByRole("button", { name: "Copy the example code" });
    await user.click(btn);
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("bb.run("));
    await waitFor(() => expect(btn).toHaveTextContent("Copied"));
    const live = btn.parentElement!.querySelector('[role="status"]')!;
    expect(live).toHaveAttribute("aria-live", "polite");
    expect(live).toHaveTextContent("Copied");
    expect(screen.getByRole("button", { name: "Copy the install command" })).toBeInTheDocument();
  });

  it("says so when copying is not possible", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
      configurable: true,
    });
    render(<SetupGuide base={BASE} />);
    await user.click(screen.getByRole("button", { name: "Copy the install command" }));
    await waitFor(() => expect(document.body.textContent).toMatch(/Could not copy/));
  });

  it("offers the run once it arrives", () => {
    render(<SetupGuide base={BASE} arrivedRunId="run_1" onDismiss={() => {}} />);
    expect(screen.getByTestId("setup-arrived")).toHaveTextContent("Your first run arrived");
    expect(screen.getByRole("link", { name: "Open the run" })).toHaveAttribute(
      "href",
      "/w/demo/projects/all/runs/run_1",
    );
    expect(screen.queryByTestId("setup-waiting")).toBeNull();
  });
});

describe("the runs list uses the guide", () => {
  it("an empty, unfiltered project shows the guide; the first run flips it to an offer", async () => {
    let runs: unknown[] = [];
    const first = fixtureRuns()[0]!.run;
    stubApi({ "/v1/runs": () => Response.json({ items: runs, next_cursor: null }) });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <Harness />
      </QueryClientProvider>,
    );
    expect(await screen.findByTestId("setup-guide")).toBeInTheDocument();
    expect(screen.getByTestId("setup-waiting")).toBeInTheDocument();
    runs = [first];
    // The list's own poll (every 15 s) is a refetch of the same query: no extra request is made by the guide.
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["runs"] });
    });
    const arrived = await screen.findByTestId("setup-arrived");
    expect(within(arrived).getByRole("link", { name: "Open the run" })).toHaveAttribute(
      "href",
      `/w/demo/projects/all/runs/${first.id}`,
    );
    // Dismissing shows the ordinary table.
    await userEvent.setup().click(within(arrived).getByRole("button", { name: "Show all runs" }));
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    expect(screen.queryByTestId("setup-guide")).toBeNull();
  });

  it("once the first run has arrived, filtering and clearing the filter does not bring the guide back (review P3-4)", async () => {
    let runs: unknown[] = [];
    const first = fixtureRuns()[0]!.run;
    stubApi({ "/v1/runs": () => Response.json({ items: runs, next_cursor: null }) });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <Harness />
      </QueryClientProvider>,
    );
    await screen.findByTestId("setup-guide");
    runs = [first];
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["runs"] });
    });
    await screen.findByTestId("setup-arrived");
    const user = userEvent.setup();
    const box = screen.getByRole("searchbox", { name: "Search runs" });
    await user.type(box, "x{Enter}");
    await waitFor(() => expect(screen.queryByTestId("setup-guide")).toBeNull());
    await user.clear(box);
    await user.type(box, "{Enter}");
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    expect(screen.queryByTestId("setup-guide")).toBeNull();
  });

  it("a filter with no matches is not the first-run guide, and the guide replaces only the list", async () => {
    stubApi({ "/v1/runs": none });
    const { unmount } = renderWithQuery(<Harness />);
    await screen.findByTestId("setup-guide");
    expect(screen.queryByRole("complementary")).toBeNull(); // no facet column beside a guide
    unmount();
    renderWithQuery(<Harness initial={{ ...DEFAULT_FILTERS, agent: "nobody" }} />);
    expect(await screen.findByText("No runs match these filters")).toBeInTheDocument();
    expect(screen.queryByTestId("setup-guide")).toBeNull();
  });
});
