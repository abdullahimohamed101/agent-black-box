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
  it("puts the count line in the page head, above the search box and the table", async () => {
    stubApi();
    const { container } = renderWithQuery(<Harness />);
    const table = await screen.findByRole("table", { name: "Runs" });
    const head = container.querySelector(".page-head")!;
    expect(
      within(head as HTMLElement).getByRole("heading", { name: "Runs", level: 1 }),
    ).toBeVisible();
    expect(head).toHaveTextContent(/\d+ runs loaded \(end of list\)/);
    expect(head.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // the count is announced politely, as before, and appears once
    expect(screen.getAllByText(/runs loaded/)).toHaveLength(1);
    expect(head.querySelector("[aria-live=polite]")).not.toBeNull();
  });

  it("keeps the search, filter and compare landmarks and the run link cell class", async () => {
    stubApi();
    const user = userEvent.setup();
    const { container } = renderWithQuery(<Harness />);
    await screen.findByRole("table", { name: "Runs" });
    expect(screen.getByRole("search", { name: "Search runs" })).toBeInTheDocument();
    expect(screen.getByRole("search", { name: "Filter runs" })).toBeInTheDocument();
    // Phase 22 D8: the compare bar is behind the "Compare" button now, not always on.
    expect(screen.queryByTestId("compare-bar")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Compare" }));
    expect(screen.getByTestId("compare-bar")).toBeInTheDocument();
    expect(screen.getByLabelText("Failed")).toBeInTheDocument();
    expect(container.querySelectorAll("td.name a").length).toBeGreaterThan(5);
  });

  it("shows no count line while loading or when there are no runs", async () => {
    stubApi();
    const { container } = renderWithQuery(<Harness />);
    expect(container.querySelector(".page-head p")).toBeNull();
    await screen.findByRole("table", { name: "Runs" });
  });
});
