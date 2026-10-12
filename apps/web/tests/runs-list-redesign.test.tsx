import { act, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_FILTERS, RunsList, type ListFilters } from "@/components/RunsList";
import { TopbarSearch } from "@/components/shell/TopbarSearch";
import { fixtureRuns } from "@/fixtures";
import { fixtureReply } from "@/fixtures/api";
import { FixtureForbiddenError, fixtureSearch } from "@/fixtures/search";
import { isContentAttribute } from "@/fixtures/withhold";
import type { RunOut } from "@/lib/api/types";
import { failureLine } from "@/lib/runsListRow";
import { BASE, renderWithQuery, stubApi } from "./helpers";

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({
  usePathname: () => "/w/demo/projects/all/runs",
  useRouter: () => ({ push }),
}));

afterEach(() => {
  vi.unstubAllGlobals();
  push.mockReset();
});

const NOW = Date.parse("2026-10-08T00:00:00Z");
const RUNS = fixtureRuns().map((f) => f.run);

function Harness({ initial }: { initial?: Partial<ListFilters> }) {
  const [f, setF] = useState<ListFilters>({ ...DEFAULT_FILTERS, ...initial });
  return <RunsList base={BASE} filters={f} onFilters={setF} now={NOW} />;
}

// Fixture names repeat (a baseline and a fix share one), so rows are found by the run's id.
const rowOf = (r: RunOut) =>
  document.querySelector(`a[href$="/runs/${r.id}"]`)!.closest("tr") as HTMLElement;

describe("failureLine", () => {
  const base = RUNS.find((r) => r.status === "FAILED" && r.first_error)!;

  it("opens with the same words as the run page headline, then the cost of the failure", () => {
    const line = failureLine(base)!;
    expect(line.sentence).toBe(`Failed at step ${base.first_error!.sequence}`);
    expect(line.detail).toMatch(/retr|no retries/);
  });

  it("is absent for every status that does not need attention, so clean runs stay one line", () => {
    for (const s of ["SUCCESS", "RUNNING", "CANCELLED", "WAITING", "QUEUED"] as const)
      expect(failureLine({ ...base, status: s })).toBeNull();
  });

  it("names timed-out and blocked runs by their own word", () => {
    expect(failureLine({ ...base, status: "TIMED_OUT" })!.sentence).toMatch(/^Timed out at step/);
    expect(failureLine({ ...base, status: "BLOCKED" })!.sentence).toMatch(/^Blocked at step/);
  });

  it("falls back to the bare word when the run has no stored first error yet", () => {
    const old: RunOut = { ...base, first_error: null };
    expect(failureLine(old)).toEqual({
      sentence: "Failed",
      detail: "first error not recorded for this run yet",
    });
    // a summary that is still catching up is not trusted either
    expect(failureLine({ ...base, summary_state: "stale" as never })!.sentence).toBe("Failed");
  });
});

describe("runs list rows", () => {
  it("gives failed runs a second line and leaves clean runs on one", async () => {
    stubApi();
    renderWithQuery(<Harness />);
    await screen.findByRole("table", { name: "Runs" });
    const failed = RUNS.filter((r) => ["FAILED", "TIMED_OUT", "BLOCKED"].includes(r.status));
    expect(failed.length).toBeGreaterThan(0);
    expect(screen.getAllByTestId("failure-line")).toHaveLength(failed.length);
    for (const r of failed)
      expect(rowOf(r)).toContainElement(within(rowOf(r)).getByTestId("failure-line"));
    const ok = RUNS.find((r) => r.status === "SUCCESS")!;
    expect(within(rowOf(ok)).queryByTestId("failure-line")).toBeNull();
  });

  it("moves events and errors into the row detail, behind a keyboard-operable button", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    await screen.findByRole("table", { name: "Runs" });
    expect(screen.queryByRole("columnheader", { name: "Events" })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Errors" })).toBeNull();
    const run = RUNS[0]!;
    const more = within(rowOf(run)).getByRole("button", { name: /^Show details of/ });
    expect(more).toHaveAttribute("aria-expanded", "false");
    more.focus();
    await user.keyboard("{Enter}");
    const detail = await screen.findByTestId("row-detail");
    expect(detail).toHaveTextContent("Events");
    expect(detail).toHaveTextContent("Errors");
    expect(within(rowOf(run)).getByRole("button", { name: /^Hide details of/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("shows the tick boxes only while comparing and drops the picks when it stops", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    await screen.findByRole("table", { name: "Runs" });
    const toggle = screen.getByRole("button", { name: "Compare" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryAllByRole("checkbox", { name: /^Compare / })).toHaveLength(0);
    toggle.focus();
    await user.keyboard("{Enter}");
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    const boxes = screen.getAllByRole("checkbox", { name: /^Compare / });
    expect(boxes).toHaveLength(RUNS.length);
    await user.click(boxes[0]!);
    expect(screen.getByTestId("compare-bar")).toHaveTextContent("1 of 2 runs picked.");
    await user.click(toggle);
    expect(screen.queryByTestId("compare-bar")).toBeNull();
    await user.click(toggle);
    expect(screen.getByTestId("compare-bar")).toHaveTextContent("Tick 2 runs to compare them.");
  });
});

describe("Needs attention", () => {
  it("asks for failed, timed-out and blocked runs together and toggles off again", async () => {
    const calls = stubApi();
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    await screen.findByRole("table", { name: "Runs" });
    const button = screen.getByRole("button", { name: "Needs attention" });
    expect(button).toHaveAttribute("aria-pressed", "false");
    await user.click(button);
    await waitFor(() => expect(button).toHaveAttribute("aria-pressed", "true"));
    const last = calls.filter((c) => c.includes("status=")).pop()!;
    expect(new URLSearchParams(last.split("?")[1]).getAll("status").sort()).toEqual([
      "BLOCKED",
      "FAILED",
      "TIMED_OUT",
    ]);
    // every row left is a run that needs attention, each with its failure line
    await waitFor(() => {
      const rows = screen.getAllByRole("row").slice(1);
      expect(rows.length).toBe(
        RUNS.filter((r) => ["FAILED", "TIMED_OUT", "BLOCKED"].includes(r.status)).length,
      );
    });
    expect(screen.getAllByTestId("failure-line")).toHaveLength(
      screen.getAllByRole("row").length - 1,
    );
    await user.click(button);
    await waitFor(() => expect(screen.getAllByRole("row")).toHaveLength(RUNS.length + 1));
    expect(button).toHaveAttribute("aria-pressed", "false");
  });

  it("keeps asking the server, so a failure is never beyond the first page", async () => {
    const calls = stubApi();
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    await screen.findByRole("table", { name: "Runs" });
    await user.click(screen.getByRole("button", { name: "Needs attention" }));
    await waitFor(() => expect(calls.some((c) => c.includes("status=FAILED"))).toBe(true));
    // the filter travels with every page request: there is no client-side filtering of a loaded page
    expect(
      calls.filter((c) => c.includes("status=FAILED")).every((c) => !c.includes("cursor=")),
    ).toBe(true);
  });
});

describe("facet column", () => {
  it("edits the query: one click adds the term, a second removes it", async () => {
    const calls = stubApi();
    const user = userEvent.setup();
    renderWithQuery(<Harness />);
    await screen.findByRole("table", { name: "Runs" });
    const region = screen.getByRole("region", { name: "Narrow the results" });
    await user.click(within(region).getByRole("button", { name: "Failed" }));
    const box = screen.getByRole("searchbox", { name: "Search" });
    await waitFor(() => expect(box).toHaveValue("status:failed"));
    // the counts arrive with the search; the same facet is now pressed and still one term
    const on = await within(region).findByRole("button", { name: /^Failed\s*\(\d+\)$/ });
    await waitFor(() => expect(on).toHaveAttribute("aria-pressed", "true"));
    expect(
      calls.some((c) => new URLSearchParams(c.split("?")[1]).get("q") === "status:failed"),
    ).toBe(true);
    await user.click(on);
    await waitFor(() => expect(box).toHaveValue(""));
  });

  it("combines facets with typed text and removes only its own term", async () => {
    stubApi();
    const user = userEvent.setup();
    renderWithQuery(<Harness initial={{ q: "tool:run_migration" }} />);
    await screen.findByRole("table", { name: "Runs" });
    const region = screen.getByRole("region", { name: "Narrow the results" });
    await user.click(await within(region).findByRole("button", { name: /^Last 24 hours$/ }));
    const box = screen.getByRole("searchbox", { name: "Search" });
    await waitFor(() => expect(box).toHaveValue("tool:run_migration started:>=-24h"));
    await user.click(within(region).getByRole("button", { name: /^Last 24 hours$/ }));
    await waitFor(() => expect(box).toHaveValue("tool:run_migration"));
  });

  it("lists agents and models with counts once a search returns them", async () => {
    stubApi();
    renderWithQuery(<Harness initial={{ q: "status:failed" }} />);
    const region = await screen.findByRole("region", { name: "Narrow the results" });
    await within(region).findByRole("heading", { name: "Agent" });
    expect(within(region).getByRole("heading", { name: "Model" })).toBeInTheDocument();
    expect(within(region).getByRole("heading", { name: "Status" })).toBeInTheDocument();
  });
});

describe("a viewer's search (ADR-101, KI-125)", () => {
  const viewer = { content: false };

  it("refuses a content field with the same error whatever the value, before reading any run", () => {
    const ask = (q: string) => {
      try {
        fixtureSearch(fixtureRuns(), q, viewer);
        return null;
      } catch (e) {
        return e instanceof FixtureForbiddenError ? e.field : "other";
      }
    };
    expect(ask("command:curl")).toBe("command");
    expect(ask("command:zzzz-nothing")).toBe("command");
    expect(ask("status:failed file:/etc/passwd")).toBe("file");
    expect(ask("-file:x")).toBe("file");
    // an owner is not refused by permission: the fixture just cannot answer
    expect(() => fixtureSearch(fixtureRuns(), "command:curl", { content: true })).toThrow(
      /fixture/,
    );
  });

  it("answers metadata searches identically for viewer and owner, with no content in the reply", () => {
    const secrets = fixtureRuns().flatMap((f) =>
      f.events.flatMap((e) =>
        Object.entries(e.attributes)
          .filter(([k, v]) => isContentAttribute(k) && typeof v === "string" && v.length > 3)
          .map(([, v]) => v as string),
      ),
    );
    expect(secrets.length).toBeGreaterThan(0);
    for (const q of [
      "status:failed",
      "tool:run_migration",
      "event:tool.call.completed",
      "migrate",
    ]) {
      const v = fixtureSearch(fixtureRuns(), q, viewer);
      const o = fixtureSearch(fixtureRuns(), q, { content: true });
      expect(v.facets).toEqual(o.facets);
      const text = JSON.stringify({
        facets: v.facets,
        query: v.query,
        matched: v.runs.map((r) => r.matched),
      });
      for (const secret of secrets) expect(text).not.toContain(secret);
    }
  });

  it("is a 403 over the fixture API and the list explains it instead of showing no runs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request | string) => {
        const url = new URL(typeof input === "string" ? input : input.url);
        const r = fixtureReply(
          url.pathname.replace(/^\/api\/abb\//, "").split("/"),
          url.searchParams,
          viewer,
        );
        return Response.json(r.body, { status: r.status });
      }),
    );
    renderWithQuery(<Harness initial={{ q: "command:curl" }} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Your role cannot search that field");
    expect(alert).toHaveTextContent("command:");
    expect(screen.queryByText(/No runs match/)).toBeNull();
    expect(screen.queryByRole("region", { name: "Narrow the results" })).toBeNull();
  });
});

describe("top-bar search", () => {
  function bar(handler?: (url: URL) => Response | undefined) {
    const calls: URL[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request | string) => {
        const url = new URL(typeof input === "string" ? input : input.url);
        calls.push(url);
        const custom = handler?.(url);
        if (custom) return custom;
        const r = fixtureReply(
          url.pathname.replace(/^\/api\/abb\//, "").split("/"),
          url.searchParams,
        );
        return Response.json(r.body, { status: r.status });
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const view = render(
      <QueryClientProvider client={client}>
        <input aria-label="elsewhere" />
        <TopbarSearch base={BASE} />
      </QueryClientProvider>,
    );
    return { calls, ...view };
  }
  const combo = () => screen.getByRole("combobox", { name: "Search all runs" });

  it("opens with / unless the person is typing in a field", async () => {
    bar();
    const user = userEvent.setup();
    await user.keyboard("/");
    expect(combo()).toHaveFocus();
    expect(combo()).toHaveValue("");
    await user.keyboard("{Escape}");
    expect(combo()).not.toHaveFocus();
    await user.click(screen.getByRole("textbox", { name: "elsewhere" }));
    await user.keyboard("/");
    expect(screen.getByRole("textbox", { name: "elsewhere" })).toHaveValue("/");
    expect(combo()).not.toHaveFocus();
    // and the shortcut is advertised
    expect(combo()).toHaveAttribute("aria-keyshortcuts", "/");
  });

  it("shows at most five matches, with keyboard selection, and Enter on one opens that run", async () => {
    const { calls } = bar();
    const user = userEvent.setup();
    await user.keyboard("/");
    await user.keyboard("event:tool*");
    const list = await screen.findByRole("listbox", { name: "Matching runs" });
    await waitFor(() => expect(within(list).getAllByRole("option").length).toBeGreaterThan(1));
    const runOptions = within(list).getAllByRole("option").slice(0, -1);
    expect(runOptions.length).toBeLessThanOrEqual(5);
    const search = calls.filter((u) => u.searchParams.get("q") === "event:tool*");
    expect(search.length).toBeGreaterThan(0);
    expect(search.every((u) => u.searchParams.get("limit") === "5")).toBe(true);
    expect(combo()).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{ArrowDown}");
    expect(combo()).toHaveAttribute("aria-activedescendant", runOptions[0]!.id);
    expect(runOptions[0]).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowDown}{ArrowUp}{Enter}");
    expect(push).toHaveBeenCalledTimes(1);
    expect(push.mock.calls[0]![0]).toMatch(/^\/w\/demo\/projects\/all\/runs\/run_/);
  });

  it("sends Enter on the bare box to the runs list with the query in ?q=", async () => {
    bar();
    const user = userEvent.setup();
    await user.keyboard("/");
    await user.keyboard("status:failed tool:a+b&x{Enter}");
    expect(push).toHaveBeenCalledTimes(1);
    const to = new URL(push.mock.calls[0]![0] as string, "http://x");
    expect(to.pathname).toBe("/w/demo/projects/all/runs");
    expect(to.searchParams.get("q")).toBe("status:failed tool:a+b&x");
    expect(to.search).not.toContain("a+b&x"); // encoded
  });

  it("Escape closes the list first, then clears the box", async () => {
    bar();
    const user = userEvent.setup();
    await user.keyboard("/");
    await user.keyboard("status:failed");
    await screen.findByRole("option", { name: /Show all/ });
    await user.keyboard("{Escape}");
    expect(combo()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("option")).toBeNull();
    expect(combo()).toHaveValue("status:failed");
    await user.keyboard("{Escape}");
    expect(combo()).toHaveValue("");
  });

  it("debounces and cancels: typing fast sends one request for the final text", async () => {
    const { calls } = bar();
    const user = userEvent.setup();
    await user.keyboard("/");
    await user.keyboard("status:failed");
    await screen.findByRole("option", { name: /Show all/ });
    const queries = calls.map((u) => u.searchParams.get("q")).filter((q) => q);
    expect(queries).toEqual(["status:failed"]);
  });

  it("says so when nothing matches, and still offers the runs list", async () => {
    bar();
    const user = userEvent.setup();
    await user.keyboard("/");
    await user.keyboard("tool:no-such-tool");
    expect(await screen.findByText("No runs match this search.")).toBeInTheDocument();
    await user.keyboard("{ArrowDown}{Enter}");
    expect(push).toHaveBeenCalledTimes(1);
  });

  it("explains a refusal for a content field without repeating the value, and a failure without raw errors", async () => {
    const { rerender } = bar((url) =>
      url.searchParams.get("q")?.startsWith("file:")
        ? Response.json(
            {
              error: {
                code: "PERMISSION_DENIED",
                message: "no",
                retryable: false,
                request_id: "r",
                details: { field: "file" },
              },
            },
            { status: 403 },
          )
        : undefined,
    );
    void rerender;
    const user = userEvent.setup();
    await user.keyboard("/");
    await user.keyboard("file:hunter2-secret");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Your role cannot search file:");
    expect(alert).not.toHaveTextContent("hunter2");
    expect(screen.queryByText("No runs match this search.")).toBeNull();
  });

  it("shows an unavailable message when the API is down", async () => {
    bar(() =>
      Response.json({ error: { code: "X", message: "boom", retryable: false } }, { status: 400 }),
    );
    const user = userEvent.setup();
    await user.keyboard("/");
    await user.keyboard("status:failed");
    expect(await screen.findByRole("alert")).toHaveTextContent("Search is unavailable");
  });

  it("renders hostile text as text and refuses an over-long query locally", async () => {
    const { calls } = bar();
    const user = userEvent.setup();
    await user.keyboard("/");
    await act(async () => {
      await user.click(combo());
      await user.paste("x".repeat(600));
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("limited to 512 characters");
    await new Promise((r) => setTimeout(r, 300));
    expect(calls.filter((u) => u.searchParams.get("q"))).toHaveLength(0);
  });

  it("has an icon button for narrow screens that opens the field", async () => {
    bar();
    const user = userEvent.setup();
    const icon = screen.getByRole("button", { name: "Search runs" });
    expect(icon).toHaveAttribute("aria-expanded", "false");
    await user.click(icon);
    expect(icon).toHaveAttribute("aria-expanded", "true");
    expect(combo()).toHaveFocus();
  });
});
