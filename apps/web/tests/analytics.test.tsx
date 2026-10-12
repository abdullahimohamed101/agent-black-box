import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Analytics } from "@/components/Analytics";
import { BarList, DayColumns } from "@/components/charts";
import { Dashboard } from "@/components/Dashboard";
import { fixtureAnalytics } from "@/fixtures/analytics";
import { windowFrom } from "@/lib/queries";
import { BASE, renderWithQuery, stubApi } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

describe("Dashboard (server aggregates, KI-028)", () => {
  it("reads the analytics summary instead of sampling runs", async () => {
    const calls = stubApi();
    renderWithQuery(<Dashboard base={BASE} />);
    expect(await screen.findByText("Success rate")).toBeVisible();
    expect(screen.getByText(/runs started in the last 7 days/)).toBeVisible();
    expect(calls.some((c) => c.startsWith("/api/abb/v1/analytics/summary"))).toBe(true);
    // the recent tables are small separate requests, never a 200-run sample
    expect(calls.filter((c) => c.includes("/v1/runs")).every((c) => !c.includes("limit=200"))).toBe(
      true,
    );
    expect(screen.getByRole("link", { name: /analytics/i })).toHaveAttribute(
      "href",
      "/w/demo/projects/all/analytics",
    );
  });

  it("shows an error with a retry when the aggregate fails", async () => {
    stubApi({
      "/analytics/summary": () =>
        Response.json(
          { error: { code: "ANALYTICS_TIMEOUT", message: "Too slow.", request_id: "req_1" } },
          { status: 503 },
        ),
    });
    renderWithQuery(<Dashboard base={BASE} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Too slow.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
  });
});

describe("Analytics page", () => {
  it("renders cost, retries, reliability and performance sections", async () => {
    stubApi();
    renderWithQuery(<Analytics base={BASE} />);
    expect(await screen.findByRole("heading", { name: "Cost" })).toBeVisible();
    expect(await screen.findByText(/of cost came from retries/)).toBeVisible();
    expect(await screen.findByRole("heading", { name: "Retry-heavy runs" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Slow operations" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Where the cost figures come from" })).toBeVisible();
  });

  it("explains runs summarised by an older version (summary version bump, Phase 9)", async () => {
    const cost = fixtureAnalytics("cost", new URLSearchParams()) as {
      headline: { unrebuilt_runs: number };
    };
    cost.headline.unrebuilt_runs = 3;
    stubApi({ "/analytics/cost": () => Response.json(cost) });
    renderWithQuery(<Analytics base={BASE} />);
    const note = await screen.findByRole("note");
    expect(note).toHaveTextContent("3 runs were summarised by an older version");
    expect(note).toHaveTextContent("per-agent figures");
    expect(note).not.toHaveTextContent("predate cost calculation");
  });

  it("shows no note when every run is current", async () => {
    stubApi();
    renderWithQuery(<Analytics base={BASE} />);
    await screen.findByText(/of cost came from retries/);
    expect(screen.queryByText(/summarised by an older version/)).toBeNull();
  });

  it("switches the window and requests it from the server", async () => {
    const calls = stubApi();
    renderWithQuery(<Analytics base={BASE} />);
    await screen.findByText(/of cost came from retries/);
    await userEvent.click(screen.getByRole("button", { name: "Last 30 days" }));
    await waitFor(() => {
      const from = windowFrom(30);
      expect(
        calls.some((c) => c.includes("/analytics/cost") && c.includes(encodeURIComponent(from))),
      ).toBe(true);
    });
    expect(screen.getByRole("button", { name: "Last 30 days" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("one failing section does not hide the others", async () => {
    stubApi({
      "/analytics/reliability": () =>
        Response.json({ error: { code: "X", message: "Reliability broke." } }, { status: 500 }),
    });
    renderWithQuery(<Analytics base={BASE} />);
    expect(await screen.findByText("Reliability broke.")).toBeVisible();
    expect(await screen.findByRole("heading", { name: "Cost" })).toBeVisible();
    expect(await screen.findByText("Run latency p50 / p95")).toBeVisible();
  });
});

describe("stale figures are marked while the next window loads", () => {
  it("dims the sections and says Updating until the new window arrives", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const calls = stubApi();
    renderWithQuery(<Analytics base={BASE} />);
    await screen.findByText(/of cost came from retries/);
    // make the next cost request wait, then switch the window
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", async (input: Request | string) => {
      const url = typeof input === "string" ? input : input.url;
      if (url.includes("/analytics/cost")) await gate;
      return original(input as Request);
    });
    await userEvent.click(screen.getByRole("button", { name: "Last 30 days" }));
    const cost = screen.getByRole("region", { name: "Cost" });
    await waitFor(() => expect(cost.querySelector("[aria-busy='true']")).not.toBeNull());
    expect(within(cost).getByText("Updating…")).toBeInTheDocument();
    release();
    await waitFor(() => expect(cost.querySelector("[aria-busy='true']")).toBeNull());
    expect(calls.length).toBeGreaterThan(0);
  });
});

describe("charts render telemetry as text", () => {
  const hostile = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
  it("bar labels and values are text nodes, never markup", () => {
    const { container } = renderWithQuery(
      <BarList
        caption="Cost by agent"
        bars={[{ label: hostile, value: 2, display: "$2.00", note: hostile }]}
      />,
    );
    expect(container.querySelector("img, script")).toBeNull();
    expect(screen.getAllByText(hostile, { exact: false }).length).toBeGreaterThan(0);
  });
  it("a single day renders one capped column with its figure (KI-064)", () => {
    renderWithQuery(
      <DayColumns
        caption="Daily spend"
        valueHeader="Cost (USD)"
        days={[{ label: "2026-10-09", value: 0.22, display: "$0.22" }]}
      />,
    );
    expect(document.querySelectorAll(".column")).toHaveLength(1);
    const table = screen.getByRole("table", { name: "Daily spend" });
    expect(within(table).getByRole("cell", { name: "$0.22" })).toBeInTheDocument();
  });
  it("day columns carry their figures in a table", () => {
    renderWithQuery(
      <DayColumns
        caption="Daily spend"
        valueHeader="Cost (USD)"
        days={[
          { label: "2026-10-06", value: 1, display: "$1.00" },
          { label: hostile, value: 3, display: "$3.00" },
        ]}
      />,
    );
    const table = screen.getByRole("table", { name: "Daily spend" });
    expect(within(table).getByRole("rowheader", { name: hostile })).toBeInTheDocument();
    expect(document.querySelector("img, script")).toBeNull();
  });
  it("is empty-safe", () => {
    renderWithQuery(<BarList caption="x" bars={[]} />);
    expect(screen.getByText("No data in this window.")).toBeVisible();
  });
});

describe("Where the money was wasted (Phase 22 D12)", () => {
  type Waste = ReturnType<typeof fixtureAnalytics> & {
    wasted_usd: number;
    failed: { spend_usd: number; runs: number; unpriced_calls: number };
    retries: { in_other_runs_usd: number };
    by_agent: { agent: string; wasted_usd: number }[];
    by_model: { model: string; wasted_usd: number }[];
  };
  const fixture = () => fixtureAnalytics("waste", new URLSearchParams()) as Waste;

  it("opens the page, above the existing cost section", async () => {
    stubApi();
    renderWithQuery(<Analytics base={BASE} />);
    const panel = await screen.findByTestId("waste-panel");
    const cost = await screen.findByRole("heading", { name: "Cost" });
    expect(
      within(panel).getByRole("heading", { name: "Where the money was wasted" }),
    ).toBeVisible();
    expect(panel.compareDocumentPosition(cost) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("shows the figures the server returned, by agent and by model", async () => {
    const w = fixture();
    expect(w.wasted_usd).toBeGreaterThan(0);
    stubApi();
    renderWithQuery(<Analytics base={BASE} />);
    const line = await screen.findByTestId("waste-line");
    expect(line).toHaveTextContent(/failed runs \$/);
    expect(line).toHaveTextContent(/retries \$/);
    expect(line).toHaveTextContent(/after the first error \$/);
    expect(line).toHaveTextContent(`(${w.failed.runs})`);
    const agents = screen.getByRole("list", { name: "Wasted spend by agent" });
    expect(within(agents).getAllByRole("listitem")).toHaveLength(w.by_agent.length);
    expect(screen.getByRole("list", { name: "Wasted spend by model" })).toBeVisible();
  });

  it("the figures reconcile: wasted is failed spend plus retries in the other runs, no dollar twice", () => {
    const w = fixture();
    expect(w.wasted_usd).toBeCloseTo(w.failed.spend_usd + w.retries.in_other_runs_usd, 9);
    expect(w.by_agent.reduce((a, b) => a + b.wasted_usd, 0)).toBeCloseTo(w.wasted_usd, 9);
  });

  it("says plainly when calls were unpriced or after-error spend is unknown", async () => {
    const w = fixture() as Record<string, unknown> & { failed: Record<string, number> };
    w.failed = { ...w.failed, unpriced_calls: 4, after_first_error_unknown_runs: 2 };
    stubApi({ "/analytics/waste": () => Response.json(w) });
    renderWithQuery(<Analytics base={BASE} />);
    expect(await screen.findByTestId("waste-unpriced")).toHaveTextContent("4 model calls");
    expect(screen.getByTestId("waste-unknown")).toHaveTextContent("2 failed runs");
  });

  it("says how many runs were summarised below the current version, and says nothing at zero (review P2-5)", async () => {
    const w = { ...fixture(), unrebuilt_runs: 7 };
    stubApi({ "/analytics/waste": () => Response.json(w) });
    const { unmount } = renderWithQuery(<Analytics base={BASE} />);
    expect(await screen.findByTestId("waste-unrebuilt")).toHaveTextContent(
      "7 runs were summarised by an older version",
    );
    unmount();
    vi.unstubAllGlobals();
    stubApi({ "/analytics/waste": () => Response.json(fixture()) });
    renderWithQuery(<Analytics base={BASE} />);
    await screen.findByTestId("waste-panel");
    expect(screen.queryByTestId("waste-unrebuilt")).toBeNull();
  });

  it("an empty window is a statement with the tape motif, and an error offers a retry", async () => {
    const w = { ...fixture(), wasted_usd: 0 };
    stubApi({ "/analytics/waste": () => Response.json(w) });
    const { unmount } = renderWithQuery(<Analytics base={BASE} />);
    const panel = await screen.findByTestId("waste-panel");
    await within(panel).findByText("Nothing was wasted in this window");
    expect(panel.querySelector("svg.illus")).not.toBeNull();
    unmount();
    vi.unstubAllGlobals();
    stubApi({
      "/analytics/waste": () =>
        Response.json(
          { error: { code: "ANALYTICS_TIMEOUT", message: "Too slow." } },
          { status: 503 },
        ),
    });
    renderWithQuery(<Analytics base={BASE} />);
    const again = await screen.findByTestId("waste-panel");
    expect(await within(again).findByRole("alert")).toHaveTextContent("Too slow.");
    expect(within(again).getByRole("button", { name: "Try again" })).toBeVisible();
  });
});
