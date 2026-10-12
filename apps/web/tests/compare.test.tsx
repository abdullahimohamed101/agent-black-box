import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CompareView } from "@/components/CompareView";
import { fixtureReply } from "@/fixtures/api";
import { BASELINE_RUN_N, WRONG_FIX_RUN_N } from "@/fixtures/codingRuns";
import { fid } from "@/fixtures/ids";
import type { ComparisonOut } from "@/lib/api/types";
import {
  deltaText,
  differenceIndexes,
  metricRows,
  nextDifference,
  scoreText,
  visibleCount,
} from "@/lib/comparison";
import { BASE } from "./helpers";

let search = "";
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(search),
}));

const LEFT = fid("run", BASELINE_RUN_N);
const RIGHT = fid("run", WRONG_FIX_RUN_N);

beforeEach(() => {
  search = `left=${LEFT}&right=${RIGHT}`;
});
afterEach(() => vi.unstubAllGlobals());

type Edit = (c: ComparisonOut) => ComparisonOut;
function stub({
  content = true,
  edit,
  status,
}: { content?: boolean; edit?: Edit; status?: number } = {}) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const url = new URL(input.url);
      calls.push(url.pathname + url.search);
      if (status)
        return Response.json(
          { error: { code: "RUN_NOT_FOUND", message: "No such run." } },
          { status },
        );
      const segs = url.pathname.replace(/^\/api\/abb\//, "").split("/");
      const r = fixtureReply(segs, url.searchParams, { content });
      const body = edit && r.status === 200 ? edit(r.body as ComparisonOut) : r.body;
      return Response.json(body, { status: r.status });
    }),
  );
  return calls;
}

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <CompareView base={BASE} />
    </QueryClientProvider>,
  );
}

describe("CompareView", () => {
  it("asks for two runs when the URL has none", () => {
    search = "";
    stub();
    show();
    expect(screen.getByText("Pick two runs to compare")).toBeInTheDocument();
  });

  it("loads, then shows the summary with signed deltas, the divergence and the outcome", async () => {
    const calls = stub();
    show();
    expect(screen.getByText("Comparing runs…")).toBeInTheDocument();
    await screen.findByTestId("compare-summary");
    expect(calls[0]).toContain(`/v1/runs/${LEFT}/compare?with_run_id=${RIGHT}`);
    const table = screen.getByTestId("compare-summary");
    const duration = table.querySelector("[data-metric=duration_ms]")!;
    expect(duration.querySelector(".delta")).toHaveAttribute("data-direction");
    expect(within(table as HTMLElement).getByText("Status")).toBeInTheDocument();
    expect(screen.getByTestId("divergence-summary")).toHaveTextContent("first diverge at step");
    expect(screen.getAllByTestId("divergence-flag")).toHaveLength(1);
    expect(screen.getAllByTestId("outcome-flag")).toHaveLength(1);
    // the divergence row is the API's `steps[index]`
    const row = screen.getByTestId("divergence-flag").closest("[data-testid=aligned-row]")!;
    expect(row).toHaveAttribute("data-divergence", "true");
    expect(screen.getByRole("link", { name: LEFT })).toHaveAttribute(
      "href",
      expect.stringContaining(LEFT),
    );
    expect(screen.queryByTestId("metadata-notice")).toBeNull();
  });

  it("a content reader sees labels and the file sets", async () => {
    stub();
    show();
    await screen.findByTestId("compare-files");
    expect(screen.getByText("Only in the left run")).toBeInTheDocument();
    expect(screen.getByTestId("compare-files")).not.toHaveAttribute("data-withheld");
    expect(screen.queryAllByText("(details withheld)", { exact: false })).toHaveLength(0);
  });

  it("a viewer gets the metadata notice, withheld labels and per-side file counts only", async () => {
    stub({ content: false });
    show();
    expect(await screen.findByTestId("metadata-notice")).toHaveTextContent(
      "Divergence is computed on metadata only",
    );
    const files = screen.getByTestId("compare-files");
    expect(files).toHaveAttribute("data-withheld", "true");
    expect(files).toHaveTextContent(/modified \d+ file/);
    expect(screen.queryByText("Only in the left run")).toBeNull();
    expect(screen.getAllByText("(details withheld)", { exact: false }).length).toBeGreaterThan(0);
  });

  it("renders hostile labels and names as text", async () => {
    const hostile = '<img src=x onerror="alert(1)">';
    stub({
      edit: (c) => ({
        ...c,
        steps: c.steps.map((r, i) =>
          i === 0 && r.left ? { ...r, left: { ...r.left, label: hostile } } : r,
        ),
        evaluations: c.evaluations.map((e) => ({ ...e, evaluator_name: hostile })),
        cost_by_model: c.cost_by_model.map((m) => ({ ...m, model: hostile })),
        files: { ...c.files, only_left: [hostile] },
      }),
    });
    const { container } = show();
    await screen.findByTestId("aligned-steps");
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain(hostile);
  });

  it("says when the alignment is partial or a run was truncated", async () => {
    stub({
      edit: (c) => ({
        ...c,
        alignment_quality: "partial",
        right: { ...c.right, truncated: true, truncated_at_event: "evt_x" },
      }),
    });
    show();
    expect(await screen.findByTestId("partial-notice")).toBeInTheDocument();
    expect(screen.getByTestId("truncated-notice")).toHaveTextContent("right run is too long");
  });

  it("states when the runs took the same steps, and when there are no steps", async () => {
    stub({
      edit: (c) => ({
        ...c,
        divergence: null,
        first_outcome_difference: null,
        steps: c.steps.map((r) => ({ ...r, relation: "equal" as const })),
      }),
    });
    show();
    expect(await screen.findByTestId("no-divergence")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Jump to divergence" })).toBeDisabled();
    vi.unstubAllGlobals();
    stub({ edit: (c) => ({ ...c, steps: [], divergence: null, first_outcome_difference: null }) });
    show();
    expect(await screen.findByTestId("no-steps")).toBeInTheDocument();
  });

  it("shows the API's error with a retry, and not found without one", async () => {
    stub({ status: 500 });
    show();
    expect(await screen.findByRole("alert")).toHaveTextContent("No such run.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    vi.unstubAllGlobals();
    stub({ status: 404 });
    show();
    await waitFor(() => expect(screen.getAllByRole("alert").at(-1)).toHaveTextContent("Not found"));
  });

  it("jumps to the divergence and moves between differences from the keyboard", async () => {
    const user = userEvent.setup();
    stub();
    show();
    await screen.findByTestId("aligned-steps");
    const diverge = document.querySelector("[data-divergence=true]") as HTMLElement;
    await user.click(screen.getByRole("button", { name: "Jump to divergence" }));
    await waitFor(() => expect(diverge).toHaveFocus());
    expect(screen.getByTestId("aligned-live")).toHaveTextContent(
      /Divergence: .* at step \d+ of \d+/,
    );
    const index = Number(diverge.getAttribute("data-index"));
    fireEvent.keyDown(diverge, { key: "ArrowDown" });
    await waitFor(() =>
      expect(document.querySelector(`[data-index="${index + 1}"]`)).toHaveFocus(),
    );
    fireEvent.keyDown(document.activeElement!, { key: "p" });
    await waitFor(() =>
      expect(screen.getByTestId("aligned-live")).toHaveTextContent(/No earlier|at step/),
    );
    fireEvent.keyDown(document.activeElement!, { key: "Home" });
    await waitFor(() => expect(document.querySelector("[data-index='0']")).toHaveFocus());
    fireEvent.keyDown(document.activeElement!, { key: "n" });
    await waitFor(() => expect(document.querySelector(`[data-index="${index}"]`)).toHaveFocus());
  });
});

describe("comparison helpers", () => {
  it("formats deltas with a sign, a word and a tone, never colour alone", () => {
    const rows = metricRows([
      { metric: "estimated_cost_usd", left: 0.1, right: 0.2, delta: 0.1 },
      { metric: "error_count", left: 2, right: 0, delta: -2 },
      { metric: "tool_calls", left: 4, right: 4, delta: 0 },
      { metric: "retry_count", left: null, right: 1, delta: null },
      { metric: "novel_metric", left: 1, right: 3, delta: 2 },
    ]);
    expect(rows[0]).toMatchObject({ label: "Cost", direction: "up", tone: "bad" });
    expect(rows[0]!.delta).toMatch(/^\+\$.*\(higher\)$/);
    expect(rows[1]).toMatchObject({ direction: "down", tone: "ok" });
    expect(rows[1]!.delta).toContain("(lower)");
    expect(rows[2]).toMatchObject({ direction: "same", delta: "no change" });
    expect(rows[3]).toMatchObject({ direction: "unknown", delta: "—" });
    expect(rows[4]).toMatchObject({ label: "novel_metric", tone: "neutral" });
  });

  it("finds differences and moves between them without wrapping", () => {
    const rows = ["equal", "changed", "equal", "left_only"].map((relation) => ({
      relation: relation as "equal",
      left: null,
      right: null,
    }));
    const diffs = differenceIndexes(rows);
    expect(diffs).toEqual([1, 3]);
    expect(nextDifference(diffs, 0, 1)).toBe(1);
    expect(nextDifference(diffs, 3, 1)).toBeNull();
    expect(nextDifference(diffs, 3, -1)).toBe(1);
    expect(nextDifference(diffs, 1, -1)).toBeNull();
  });

  it("always includes the row a jump asked for", () => {
    expect(visibleCount(400, 5000, null)).toBe(400);
    expect(visibleCount(400, 5000, 1200)).toBe(1221);
    expect(visibleCount(400, 100, 50)).toBe(100);
  });

  it("writes scores and score changes", () => {
    expect(scoreText(null)).toBe("—");
    expect(scoreText({ score: 1, label: "pass" })).toBe("1 · pass");
    expect(deltaText(-0.5)).toBe("−0.5");
    expect(deltaText(0)).toBe("no change");
    expect(deltaText(null)).toBe("—");
  });
});
