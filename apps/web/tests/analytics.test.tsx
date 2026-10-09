import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Analytics } from "@/components/Analytics";
import { BarList, DayColumns } from "@/components/charts";
import { Dashboard } from "@/components/Dashboard";
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
