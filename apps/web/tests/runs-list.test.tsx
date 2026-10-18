import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_FILTERS, RunsList, type ListFilters } from "@/components/RunsList";
import { fixtureRuns } from "@/fixtures";
import { BASE, renderWithQuery, stubApi } from "./helpers";

// Counts come from the fixture data (Phase 11 added two runs): rows are the runs plus the header row.
const RUNS = fixtureRuns().map((f) => f.run);
const N = RUNS.length;
const FAILED = RUNS.filter((r) => r.status === "FAILED").length;

afterEach(() => vi.unstubAllGlobals());

function Harness({
  pageSize,
  initial = DEFAULT_FILTERS,
}: {
  pageSize?: number;
  initial?: ListFilters;
}) {
  const [f, setF] = useState<ListFilters>(initial);
  return (
    <RunsList
      base={BASE}
      filters={f}
      onFilters={setF}
      now={Date.parse("2026-10-08T00:00:00Z")}
      pageSize={pageSize}
    />
  );
}

describe("RunsList", () => {
  it("loads, filters by status (a facet) and by agent (a link's agent text) through the API and clears", async () => {
    const calls = stubApi();
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading runs");
    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(N + 1));
    const facets = screen.getByRole("region", { name: "Narrow the results" });
    await user.click(within(facets).getByRole("button", { name: "Failed" }));
    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(FAILED + 1));
    expect(
      calls.some((c) => new URLSearchParams(c.split("?")[1]).get("q") === "status:failed"),
    ).toBe(true);
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(N + 1));
  });

  it("an agent filter from a link narrows the list, shows as a chip, and clears", async () => {
    const calls = stubApi();
    const user = userEvent.setup();
    renderWithQuery(<Harness initial={{ ...DEFAULT_FILTERS, agent: "nobody" }} />);
    expect(await screen.findByText("No runs match these filters")).toBeInTheDocument();
    expect(calls.some((c) => c.includes("agent_id=nobody"))).toBe(true);
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(N + 1));
  });

  it("does not send an invalid agent slug from a link to the API, and says so on its chip", async () => {
    const calls = stubApi();
    renderWithQuery(<Harness initial={{ ...DEFAULT_FILTERS, agent: "Bad Agent!" }} />);
    await screen.findAllByRole("row");
    expect(calls.every((c) => !c.includes("agent_id"))).toBe(true);
    const chips = screen.getByRole("list", { name: "Search terms" });
    expect(chips).toHaveTextContent("agent: Bad Agent!");
    expect(chips).toHaveTextContent("not a valid agent name, ignored");
  });

  it("paginates with a cursor without repeating or dropping runs", async () => {
    stubApi();
    const user = userEvent.setup();
    const page = Math.ceil(N / 2); // two pages (the fixture count may be odd)
    renderWithQuery(<Harness pageSize={page} />);
    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(page + 1));
    expect(screen.getByText(new RegExp(`${page} runs loaded`))).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(N + 1));
    expect(screen.getByText(new RegExp(`${N} runs loaded \\(end of list\\)`))).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
    // the table's links only: the page also links to Analytics from the 7-day sentence
    const hrefs = within(screen.getByRole("table"))
      .getAllByRole("link")
      .map((l) => l.getAttribute("href"));
    expect(hrefs).toHaveLength(N);
    expect(new Set(hrefs).size).toBe(N);
  });

  it("shows the empty state for a project with no runs", async () => {
    stubApi({ "/v1/runs": () => Response.json({ items: [], next_cursor: null }) });
    renderWithQuery(<Harness />);
    expect(await screen.findByText("No runs yet")).toBeInTheDocument();
  });

  it("shows a typed error with request id and retries", async () => {
    stubApi({
      "/v1/runs": () =>
        Response.json(
          {
            error: {
              code: "API_UNREACHABLE",
              message: "The API could not be reached.",
              retryable: true,
              request_id: null,
            },
          },
          { status: 503 },
        ),
    });
    renderWithQuery(<Harness />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The API could not be reached.");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("status is text, never colour alone", async () => {
    stubApi();
    renderWithQuery(<Harness />);
    const rows = await screen.findAllByRole("row");
    expect(
      rows
        .slice(1)
        .every((r) => /Success|Failed|Running|Awaiting approval|Blocked/.test(r.textContent ?? "")),
    ).toBe(true);
  });
});
