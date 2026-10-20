import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_FILTERS, RunsList, type ListFilters } from "@/components/RunsList";
import { BASE, renderWithQuery, stubApi } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

function Harness() {
  const [f, setF] = useState<ListFilters>(DEFAULT_FILTERS);
  return (
    <RunsList base={BASE} filters={f} onFilters={setF} now={Date.parse("2026-10-08T00:00:00Z")} />
  );
}

describe("runs list header", () => {
  it("puts the title line first, then the field, then the count line, then the table", async () => {
    stubApi();
    const { container } = renderWithQuery(<Harness />);
    const table = await screen.findByRole("table", { name: "Runs" });
    const title = container.querySelector(".page-title")!;
    expect(
      within(title as HTMLElement).getByRole("heading", { name: "Runs", level: 1 }),
    ).toBeVisible();
    // the count moved from the page head to the chip row under the field (design pass 2): once, announced politely
    const row = container.querySelector(".chip-row")!;
    expect(row).toHaveTextContent(/\d+ runs loaded \(end of list\)/);
    expect(screen.getAllByText(/runs loaded/)).toHaveLength(1);
    expect(row.querySelector("[aria-live=polite]")).not.toBeNull();
    const field = screen.getByRole("search", { name: "Search runs" });
    const follows = (a: Element, b: Element) =>
      (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
    expect(follows(title, field)).toBe(true);
    expect(follows(field, row)).toBe(true);
    expect(follows(row, table)).toBe(true);
  });

  it("keeps the search and compare landmarks and the run link cell class; the old filter row is gone", async () => {
    stubApi();
    const user = userEvent.setup();
    const { container } = renderWithQuery(<Harness />);
    await screen.findByRole("table", { name: "Runs" });
    expect(screen.getByRole("search", { name: "Search runs" })).toBeInTheDocument();
    // one field instead of three filtering systems: no filter form, no Agent or Started control, no Search button
    expect(screen.queryByRole("search", { name: "Filter runs" })).toBeNull();
    expect(screen.queryByLabelText("Agent")).toBeNull();
    expect(screen.queryByLabelText("Started")).toBeNull();
    expect(screen.queryByRole("button", { name: "Search" })).toBeNull();
    // Phase 22 D8: the compare bar is behind the "Compare" button, not always on.
    expect(screen.queryByTestId("compare-bar")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Compare" }));
    expect(screen.getByTestId("compare-bar")).toBeInTheDocument();
    // status is a facet now
    const facets = screen.getByRole("region", { name: "Narrow the results" });
    expect(within(facets).getByRole("button", { name: "Failed" })).toBeInTheDocument();
    expect(container.querySelectorAll("td.name a").length).toBeGreaterThan(5);
  });

  it("shows no count line while loading or when there are no runs", async () => {
    stubApi();
    const { container } = renderWithQuery(<Harness />);
    expect(container.querySelector(".chip-row")).not.toHaveTextContent(/runs loaded/);
    await screen.findByRole("table", { name: "Runs" });
  });
});
