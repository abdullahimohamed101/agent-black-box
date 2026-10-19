import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Analytics } from "@/components/Analytics";
import { BarList, DayColumns } from "@/components/charts";
import {
  costHeadline,
  parseReport,
  performanceHeadline,
  reliabilityHeadline,
  windowPhrase,
} from "@/copy/analytics";
import { fixtureAnalytics } from "@/fixtures/analytics";
import { BASE, renderWithQuery, stubApi } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

// Step 5d: each report opens with ONE sentence in ink, derived from the server's headline figures only.
describe("report headlines", () => {
  it("names the window in words", () => {
    expect(windowPhrase(1)).toBe("today");
    expect(windowPhrase(7)).toBe("in the last 7 days");
  });
  it("cost: total, per run and per successful run, units on every number", () => {
    expect(
      costHeadline(
        { total_usd: 8, per_run_usd: 2, per_successful_run_usd: 2.25, unpriced_calls: 0 },
        7,
      ),
    ).toBe("$8.00 spent in the last 7 days, $2.00 per run and $2.25 per successful run.");
  });
  it("cost: leaves out a per-run figure the server did not send, and says unpriced calls count as $0", () => {
    expect(
      costHeadline(
        { total_usd: 0.5, per_run_usd: null, per_successful_run_usd: null, unpriced_calls: 1 },
        1,
      ),
    ).toBe("$0.50 spent today. 1 model call has no price and counts as $0.");
    expect(
      costHeadline(
        { total_usd: 1, per_run_usd: 0.5, per_successful_run_usd: null, unpriced_calls: 3 },
        30,
      ),
    ).toBe(
      "$1.00 spent in the last 30 days, $0.50 per run. 3 model calls have no price and count as $0.",
    );
  });
  it("reliability: success first, only the non-zero bad rates, then retries", () => {
    expect(
      reliabilityHeadline(
        { success_rate: 0.5, failure_rate: 0.25, timeout_rate: 0.25, retry_rate: 0.1 },
        7,
      ),
    ).toBe(
      "50% of finished runs succeeded in the last 7 days; 25% failed or were blocked and 25% timed out. 10% of all runs had at least one retry.",
    );
    expect(
      reliabilityHeadline({ success_rate: 1, failure_rate: 0, timeout_rate: 0, retry_rate: 0 }, 1),
    ).toBe("100% of finished runs succeeded today; none failed, were blocked or timed out.");
    // a rate that only rounds to 0% is not "none"
    expect(
      reliabilityHeadline(
        { success_rate: 0.997, failure_rate: 0.003, timeout_rate: 0.002, retry_rate: 0 },
        7,
      ),
    ).toBe(
      "100% of finished runs succeeded in the last 7 days; under 1% failed or were blocked and under 1% timed out.",
    );
    expect(
      reliabilityHeadline(
        { success_rate: 0.99, failure_rate: 0, timeout_rate: 0.004, retry_rate: 0.004 },
        7,
      ),
    ).toBe(
      "99% of finished runs succeeded in the last 7 days; under 1% timed out. Under 1% of all runs had at least one retry.",
    );
    expect(
      reliabilityHeadline(
        { success_rate: null, failure_rate: null, timeout_rate: null, retry_rate: null },
        7,
      ),
    ).toBe("No runs finished in the last 7 days.");
  });
  it("performance: the run median and 95th percentile in words, or nothing timed", () => {
    expect(performanceHeadline({ p50_ms: 5100, p95_ms: 12400 }, 7)).toBe(
      "Half of runs finished within 5.1 s and 95% within 12 s in the last 7 days.",
    );
    expect(performanceHeadline({ p50_ms: null, p95_ms: null }, 1)).toBe("No timed runs today.");
  });
  it("an unknown or missing tab is Cost", () => {
    expect(parseReport("performance")).toBe("performance");
    expect(parseReport("nope")).toBe("cost");
    expect(parseReport(null)).toBe("cost");
  });
});

describe("the report tabs", () => {
  it("is the shared tab list: arrow keys move and select, Home and End jump, each tab has one panel", async () => {
    stubApi();
    renderWithQuery(<Analytics base={BASE} />);
    const list = screen.getByRole("tablist", { name: "Reports" });
    const tabs = within(list).getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Cost", "Reliability", "Performance"]);
    tabs[0]!.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(tabs[1]).toHaveAttribute("aria-selected", "true");
    expect(tabs[1]).toHaveFocus();
    expect(screen.getByRole("tabpanel", { name: "Reliability" })).toBeVisible();
    await userEvent.keyboard("{End}");
    expect(tabs[2]).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowRight}");
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{Home}");
    expect(tabs[0]).toHaveFocus();
    expect(tabs[1]).toHaveAttribute("tabindex", "-1");
  });

  it("every report opens with its one headline sentence", async () => {
    stubApi();
    renderWithQuery(<Analytics base={BASE} />);
    for (const [tab, re] of [
      ["Cost", /spent in the last 7 days/],
      ["Reliability", /of finished runs succeeded in the last 7 days/],
      ["Performance", /Half of runs finished within/],
    ] as const) {
      await userEvent.click(screen.getByRole("tab", { name: tab }));
      const panel = screen.getByRole("tabpanel", { name: tab });
      const lines = await within(panel).findAllByTestId("report-headline");
      expect(lines).toHaveLength(1);
      expect(lines[0]).toHaveTextContent(re);
    }
  });
});

describe("the exclusion note (ADR-161) is a notice", () => {
  it("is a note in the shared notice style, with the same words", async () => {
    const body = fixtureAnalytics("cost", new URLSearchParams()) as Record<string, unknown>;
    stubApi({ "/analytics/cost": () => Response.json({ ...body, excluded_expired_traces: 2 }) });
    renderWithQuery(<Analytics base={BASE} />);
    const note = await screen.findByTestId("expired-traces");
    expect(note).toHaveAttribute("role", "note");
    expect(note).toHaveClass("notice");
    expect(note).toHaveTextContent(
      "Excludes 2 runs whose trace has expired: the cost by agent, model and source need the trace detail. Run counts, totals and the cost headline include them.",
    );
  });
});

describe("chart marks have text equivalents", () => {
  it("a bar carries its value as text; a lead bar is marked, and never alone", () => {
    renderWithQuery(
      <BarList
        caption="Success rate by variant"
        marks="lead"
        bars={[
          { label: "baseline", value: 1, display: "100%", lead: true },
          { label: "wrong fix", value: 0, display: "0%" },
        ]}
      />,
    );
    const items = within(
      screen.getByRole("list", { name: "Success rate by variant" }),
    ).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    for (const li of items) expect(li.textContent).toMatch(/\d+%/);
    expect(items[0]).toHaveAttribute("data-lead", "true");
    expect(items[1]).not.toHaveAttribute("data-lead");
  });

  it("day columns say the tallest day in words and keep a table of every figure; the tall mark is the one marked", () => {
    const { container } = renderWithQuery(
      <DayColumns
        caption="Share of finished runs that failed"
        valueHeader="Failure rate"
        peak={(d, l) => `Highest: ${d} on ${l}`}
        days={[
          { label: "2026-10-06", value: 0.1, display: "10% of 10" },
          { label: "2026-10-07", value: 0.5, display: "50% of 4" },
        ]}
      />,
    );
    expect(screen.getByText(/Highest: 50% of 4 on 2026-10-07/)).toBeVisible();
    const table = screen.getByRole("table", { name: "Share of finished runs that failed" });
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(container.querySelectorAll('.column[data-peak="true"]')).toHaveLength(1);
    expect(container.querySelector("[data-tone]")).toBeNull();
  });

  it("a chart of fewer than three days prints each value beside its column; three or more do not", () => {
    const day = (n: number) => ({ label: `2026-10-0${n}`, value: n, display: `${n} runs` });
    const few = renderWithQuery(<DayColumns caption="a" valueHeader="v" days={[day(1), day(2)]} />);
    expect(few.container.querySelector(".column-row")).toHaveAttribute("data-few", "true");
    expect(
      Array.from(few.container.querySelectorAll(".column-value")).map((e) => e.textContent),
    ).toEqual(["1 runs", "2 runs"]);
    const many = renderWithQuery(
      <DayColumns caption="b" valueHeader="v" days={[day(1), day(2), day(3)]} />,
    );
    expect(many.container.querySelector(".column-value")).toBeNull();
    expect(many.container.querySelector(".column-row")).not.toHaveAttribute("data-few");
  });

  it("no mark has a failure tone: the failure trend is neutral and ink, not red", () => {
    const { container } = renderWithQuery(
      <DayColumns
        caption="x"
        valueHeader="y"
        days={[{ label: "2026-10-07", value: 1, display: "100%" }]}
      />,
    );
    expect(container.innerHTML).not.toMatch(/bad|fault|red/);
  });
});
