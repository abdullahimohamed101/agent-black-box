import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import WorkspaceHome from "@/app/w/[workspace]/page";
import ProjectHome from "@/app/w/[workspace]/projects/[project]/page";
import { RunsWeek } from "@/components/RunsWeek";
import { weekParts, weekSentence, weekText } from "@/copy/runs";
import { fixtureRuns } from "@/fixtures";
import type { AnalyticsSummary } from "@/lib/api/types";
import { formatCost, num } from "@/lib/format";
import { BASE, renderWithQuery, stubApi } from "./helpers";

// Design pass 2 step 5a: the Overview page is merged into Runs. Its five stat blocks become one sentence above the list,
// derived from the same server aggregate; its routes redirect so no link breaks.
vi.mock("next/navigation", () => ({
  redirect: (to: string) => {
    throw new Error(`redirect:${to}`);
  },
}));

afterEach(() => vi.unstubAllGlobals());

const summary = (over: Partial<AnalyticsSummary["runs"]> = {}, usd = 22.15): AnalyticsSummary =>
  ({
    runs: {
      total: 12,
      active: 2,
      finished: 10,
      success: 8,
      failed: 2,
      timed_out: 1,
      blocked: 0,
      cancelled: 0,
      ...over,
    },
    cost: { total_usd: usd, unpriced_calls: 0 },
  }) as unknown as AnalyticsSummary;

describe("the 7-day sentence", () => {
  it("is counts in words: runs, the success rate, failures (failed, timed out and blocked together), spend, in progress", () => {
    expect(weekText(weekParts(summary()))).toBe(
      "Last 7 days: 12 runs, 80% succeeded, 3 failed, $22.15 spent, 2 in progress.",
    );
  });

  it("says zero in words, drops the in-progress clause at zero, and singularises one run", () => {
    expect(weekText(weekParts(summary({ failed: 0, timed_out: 0, active: 0, success: 10 })))).toBe(
      "Last 7 days: 12 runs, 100% succeeded, none failed, $22.15 spent.",
    );
    expect(
      weekText(
        weekParts(
          summary({ total: 1, finished: 0, success: 0, failed: 0, timed_out: 0, active: 1 }),
        ),
      ),
    ).toBe("Last 7 days: 1 run, none failed, $22.15 spent, 1 in progress.");
  });

  it("splits into the pieces a phone keeps (runs and failures) and the pieces it drops", () => {
    const s = weekSentence(weekParts(summary()));
    expect(s.lead).toBe("Last 7 days: 12 runs");
    expect(s.failed).toBe("3 failed");
    expect(s.extra).toEqual(["80% succeeded"]);
    expect(s.tail).toEqual(["$22.15 spent", "2 in progress"]);
  });

  it("is derived from the fixture runs through the analytics summary, and links to Analytics", async () => {
    const calls = stubApi();
    const runs = fixtureRuns().map((f) => f.run);
    const failed = runs.filter((r) => ["FAILED", "TIMED_OUT", "BLOCKED"].includes(r.status)).length;
    const spend = formatCost(
      runs.reduce((n, r) => n + (num(r.summary["estimated_cost_usd"]) ?? 0), 0),
    );
    renderWithQuery(<RunsWeek base={BASE} />);
    const sentence = await screen.findByTestId("week-sentence");
    expect(sentence).toHaveTextContent(`Last 7 days: ${runs.length} runs`);
    expect(sentence).toHaveTextContent(`${failed} failed`);
    expect(sentence).toHaveTextContent(`${spend} spent`);
    expect(calls.some((c) => c.startsWith("/api/abb/v1/analytics/summary"))).toBe(true);
    // the aggregate, never a sample of runs
    expect(calls.filter((c) => c.includes("/v1/runs")).length).toBe(0);
    expect(screen.getByRole("link", { name: "Analytics" })).toHaveAttribute(
      "href",
      "/w/demo/projects/all/analytics",
    );
  });

  it("is silent when the aggregate fails or the window holds no runs (the list speaks instead)", async () => {
    stubApi({
      "/analytics/summary": () =>
        Response.json({ error: { code: "X", message: "down", retryable: true } }, { status: 503 }),
    });
    const first = renderWithQuery(<RunsWeek base={BASE} />);
    await waitFor(() => expect(screen.queryByTestId("week-sentence")).toBeNull());
    first.unmount();
    stubApi();
    renderWithQuery(<RunsWeek base={{ ...BASE, project: "prj_01J9ZZZZZZZZZZZZZZZZZZZZZZ" }} />);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("week-sentence")).toBeNull();
  });
});

describe("old routes redirect to Runs in the same scope", () => {
  it("/w/{workspace} (the project list) goes to all projects' runs", async () => {
    await expect(WorkspaceHome({ params: Promise.resolve({ workspace: "acme" }) })).rejects.toThrow(
      "redirect:/w/acme/projects/all/runs",
    );
  });

  it("/w/{workspace}/projects/{project} (Overview) goes to that project's runs", async () => {
    await expect(
      ProjectHome({ params: Promise.resolve({ workspace: "acme", project: "web" }) }),
    ).rejects.toThrow("redirect:/w/acme/projects/web/runs");
    await expect(
      ProjectHome({ params: Promise.resolve({ workspace: "acme", project: "all" }) }),
    ).rejects.toThrow("redirect:/w/acme/projects/all/runs");
  });
});
