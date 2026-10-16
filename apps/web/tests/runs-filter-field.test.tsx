import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RunsListPage, parseFilters } from "@/components/RunsListPage";
import { BASE, renderWithQuery, stubApi } from "./helpers";

// Design pass 2 step 5a: the toggle row, agent field, started select, Search button and help disclosure are one filter field
// with chips, plus facets that write chips. The URL parameter names are unchanged, so every old link still works.
vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/projects/all/runs",
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

beforeEach(() => history.replaceState(null, "", "/w/demo/projects/all/runs"));
afterEach(() => {
  vi.unstubAllGlobals();
  history.replaceState(null, "", "/");
});

const field = () => screen.getByRole("searchbox", { name: "Search runs" });

describe("deep links", () => {
  it("reads q, status, agent and range exactly as before", () => {
    const f = parseFilters(
      new URLSearchParams("q=tool%3Ax&status=FAILED,BLOCKED&agent=coding-agent&range=24h"),
    );
    // a lone agent slug becomes a term in the query; several statuses stay the legacy parameter (the grammar cannot OR)
    expect(f).toEqual({
      q: "tool:x agent:coding-agent",
      statuses: ["FAILED", "BLOCKED"],
      agent: "",
      range: "24h",
    });
    expect(parseFilters(new URLSearchParams("status=FAILED")).q).toBe("status:failed");
    expect(parseFilters(new URLSearchParams("range=bogus")).range).toBe("all");
  });

  it("opens with the field, chips and rows of a shared link", async () => {
    history.replaceState(null, "", "/w/demo/projects/all/runs?q=status%3Afailed");
    stubApi();
    renderWithQuery(<RunsListPage base={BASE} />);
    await waitFor(() => expect(field()).toHaveValue("status:failed"));
    const chips = await screen.findByRole("list", { name: "Search terms" });
    expect(within(chips).getByText("status:failed")).toBeInTheDocument();
  });
});

describe("the one field", () => {
  it("commits on Enter, writes the URL, and has no Search button", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<RunsListPage base={BASE} />);
    await screen.findByRole("table", { name: "Runs" });
    expect(screen.queryByRole("button", { name: "Search" })).toBeNull();
    await user.type(field(), "tool:run_migration{Enter}");
    await waitFor(() => expect(location.search).toBe("?q=tool%3Arun_migration"));
    expect(await screen.findByRole("list", { name: "Search terms" })).toHaveTextContent(
      "tool:run_migration",
    );
  });

  it("a facet writes a chip and the URL, and removing the chip clears both", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<RunsListPage base={BASE} />);
    await screen.findByRole("table", { name: "Runs" });
    const facets = screen.getByRole("region", { name: "Narrow the results" });
    await user.click(within(facets).getByRole("button", { name: "Failed" }));
    await waitFor(() => expect(location.search).toBe("?q=status%3Afailed"));
    const chips = await screen.findByRole("list", { name: "Search terms" });
    await user.click(within(chips).getByRole("button", { name: "Remove status:failed" }));
    await waitFor(() => expect(location.search).toBe(""));
    expect(field()).toHaveValue("");
  });

  it("Needs attention is a facet preset that shows as one chip, keeps the legacy status parameter, and clears", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<RunsListPage base={BASE} />);
    await screen.findByRole("table", { name: "Runs" });
    const facets = screen.getByRole("region", { name: "Narrow the results" });
    await user.click(within(facets).getByRole("button", { name: "Needs attention" }));
    await waitFor(() => expect(location.search).toBe("?status=FAILED%2CTIMED_OUT%2CBLOCKED"));
    const chips = await screen.findByRole("list", { name: "Search terms" });
    expect(chips).toHaveTextContent("status: failed, timed out, blocked");
    await user.click(within(chips).getByRole("button", { name: /^Remove status: failed/ }));
    await waitFor(() => expect(location.search).toBe(""));
  });

  it("the ? button opens the grammar and the fields the API allows, and closes again", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<RunsListPage base={BASE} />);
    await screen.findByRole("table", { name: "Runs" });
    const help = screen.getByRole("button", { name: "Search help" });
    expect(help).toHaveAttribute("aria-expanded", "false");
    await user.click(help);
    expect(help).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByRole("list", { name: "Searchable fields" })).toHaveTextContent(
      "status:",
    );
    expect(screen.getByText(/Terms are combined with AND/)).toBeInTheDocument();
    await user.click(help);
    expect(screen.queryByRole("list", { name: "Searchable fields" })).toBeNull();
  });

  it("clear filters resets the field, the chips and the URL in one click", async () => {
    history.replaceState(null, "", "/w/demo/projects/all/runs?q=status%3Afailed&range=24h");
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<RunsListPage base={BASE} />);
    await waitFor(() => expect(field()).toHaveValue("status:failed"));
    await user.click(await screen.findByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(location.search).toBe(""));
    expect(field()).toHaveValue("");
  });
});
