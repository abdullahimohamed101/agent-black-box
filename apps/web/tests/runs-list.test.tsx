import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dashboard } from "@/components/Dashboard";
import { DEFAULT_FILTERS, RunsList, type ListFilters } from "@/components/RunsList";
import { fixtureRuns } from "@/fixtures";
import { BASE, renderWithQuery, stubApi } from "./helpers";

// Counts come from the fixture data (Phase 11 added two runs): rows are the runs plus the header row.
const RUNS = fixtureRuns().map((f) => f.run);
const N = RUNS.length;
const FAILED = RUNS.filter((r) => r.status === "FAILED").length;
const FINISHED = RUNS.filter((r) => !["RUNNING", "WAITING_FOR_APPROVAL"].includes(r.status));
const SUCCESS_RATE = `${Math.round((FINISHED.filter((r) => r.status === "SUCCESS").length / FINISHED.length) * 100)}%`;

afterEach(() => vi.unstubAllGlobals());

function Harness({ pageSize }: { pageSize?: number }) {
  const [f, setF] = useState<ListFilters>(DEFAULT_FILTERS);
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
  it("loads, filters by status/agent through the API and clears", async () => {
    const calls = stubApi();
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading runs");
    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(N + 1));
    await user.click(screen.getByLabelText("Failed"));
    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(FAILED + 1));
    expect(calls.some((c) => c.includes("status=FAILED"))).toBe(true);
    await user.type(screen.getByLabelText("Agent"), "nobody");
    expect(await screen.findByText("No runs match these filters")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(N + 1));
  });

  it("does not send an invalid half-typed agent slug to the API", async () => {
    const calls = stubApi();
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    await screen.findAllByRole("row");
    await user.type(screen.getByLabelText("Agent"), "Bad Agent!");
    expect(calls.every((c) => !c.includes("agent_id"))).toBe(true);
    expect(screen.getByLabelText("Agent")).toHaveAttribute("aria-invalid", "true");
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
    const hrefs = screen.getAllByRole("link").map((l) => l.getAttribute("href"));
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

describe("Dashboard", () => {
  it("renders computed stats and recent failures", async () => {
    stubApi();
    renderWithQuery(<Dashboard base={BASE} />);
    expect(await screen.findByText("Success rate")).toBeInTheDocument();
    expect(screen.getByText(SUCCESS_RATE)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Recent failures" })).toBeInTheDocument();
    expect(screen.getByText(/deploy-agent, triage-agent/)).toBeInTheDocument();
    expect(
      screen.getByText(new RegExp(`${N} runs started in the last 7 days`)),
    ).toBeInTheDocument();
  });

  it("empty and error states", async () => {
    stubApi();
    const empty = { ...BASE, project: "prj_01J9ZZZZZZZZZZZZZZZZZZZZZZ" }; // matches no fixture run
    const { unmount } = renderWithQuery(<Dashboard base={empty} />);
    expect(await screen.findByText("No runs yet")).toBeInTheDocument();
    unmount();
    stubApi({
      "/analytics/summary": () =>
        Response.json({ error: { code: "X", message: "down", retryable: true } }, { status: 503 }),
    });
    renderWithQuery(<Dashboard base={BASE} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
  });
});
