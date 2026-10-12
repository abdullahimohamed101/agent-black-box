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
  deltaLine,
  describeStep,
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
    // Phase 22 D12: the headline names the step and what each side did there (was "first diverge at step").
    expect(screen.getByTestId("divergence-summary")).toHaveTextContent(
      /diverged at step \d+: A .+, B .+\./,
    );
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

  it("opens with the divergence line, one mono line of deltas, and the steps before the measures", async () => {
    stub();
    show();
    const line = await screen.findByTestId("divergence-summary");
    const compare = screen.getByTestId("compare");
    const deltas = screen.getByTestId("compare-deltas");
    // Order: headline, deltas, then the aligned steps, then the measures table.
    const order = [
      line,
      deltas,
      screen.getByTestId("aligned-steps"),
      screen.getByTestId("compare-summary"),
    ];
    for (let i = 1; i < order.length; i++)
      expect(
        order[i - 1]!.compareDocumentPosition(order[i]!) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    expect(compare.querySelectorAll("[data-delta]")).toHaveLength(5);
    expect(
      [...compare.querySelectorAll("[data-delta]")].map((e) => e.getAttribute("data-delta")),
    ).toEqual(["duration", "cost", "calls", "retries", "files"]);
    // The full table is still there, behind a disclosure.
    expect(screen.getByText("All measures").closest("details")).not.toHaveAttribute("open");
  });

  it("names what each side did at the divergence, with names as text", async () => {
    const hostile = "<b>x</b>";
    stub({
      edit: (c) => {
        const i = c.divergence!.index;
        return {
          ...c,
          steps: c.steps.map((r, k) =>
            k === i
              ? {
                  relation: "changed" as const,
                  left: {
                    ...r.left!,
                    event_type: "tool.completed",
                    label: "run_tests",
                    label_withheld: false,
                  },
                  right: {
                    ...r.right!,
                    event_type: "file.modified",
                    label: hostile,
                    label_withheld: false,
                  },
                }
              : r,
          ),
        };
      },
    });
    const { container } = show();
    const line = await screen.findByTestId("divergence-summary");
    expect(line).toHaveTextContent("diverged at step");
    expect(line.textContent).toMatch(/A called run_tests, B edited <b>x<\/b>\.$/);
    expect(line.querySelector("b")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
  });

  it("a viewer's line never carries withheld content", async () => {
    stub({
      content: false,
      edit: (c) => {
        const i = c.divergence!.index;
        return {
          ...c,
          steps: c.steps.map((r, k) =>
            k === i
              ? {
                  relation: "changed" as const,
                  left: {
                    ...r.left!,
                    event_type: "shell.command.completed",
                    label: null,
                    label_withheld: true,
                  },
                  right: {
                    ...r.right!,
                    event_type: "file.modified",
                    label: null,
                    label_withheld: true,
                  },
                }
              : r,
          ),
        };
      },
    });
    show();
    const line = await screen.findByTestId("divergence-summary");
    expect(line.textContent).toContain("details withheld for your role");
    expect(line.textContent).toMatch(
      /A ran a shell command \(details withheld for your role\), B edited a file/,
    );
  });

  it("a side with no step at the divergence says so", async () => {
    stub({
      edit: (c) => {
        const i = c.divergence!.index;
        return {
          ...c,
          divergence: { ...c.divergence!, relation: "left_only" as const },
          steps: c.steps.map((r, k) =>
            k === i ? { relation: "left_only" as const, left: r.left, right: null } : r,
          ),
        };
      },
    });
    show();
    expect((await screen.findByTestId("divergence-summary")).textContent).toMatch(
      /B had no step here\./,
    );
  });

  it("folds the identical steps before the divergence and unfolds them on request or on a jump", async () => {
    const user = userEvent.setup();
    stub();
    show();
    await screen.findByTestId("aligned-steps");
    const index = Number(
      (document.querySelector("[data-divergence=true]") as HTMLElement).getAttribute("data-index"),
    );
    expect(index).toBeGreaterThan(4); // the fixture pair parts well into the run
    const rows = () => screen.getAllByTestId("aligned-row");
    expect(rows()[0]).toHaveAttribute("data-index", String(index - 3));
    await user.click(screen.getByTestId("show-earlier-steps"));
    expect(rows()[0]).toHaveAttribute("data-index", "0");
    expect(screen.queryByTestId("show-earlier-steps")).toBeNull();
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

  it("builds the one-line deltas: calls sum model and tool calls, unknown stays a dash", () => {
    const line = deltaLine([
      { metric: "duration_ms", left: 1000, right: 3500, delta: 2500 },
      { metric: "estimated_cost_usd", left: 0.2, right: 0.1, delta: -0.1 },
      { metric: "llm_calls", left: 2, right: 3, delta: 1 },
      { metric: "tool_calls", left: 5, right: 7, delta: 2 },
      { metric: "retry_count", left: null, right: 1, delta: null },
      { metric: "files_modified", left: 2, right: 2, delta: 0 },
    ]);
    expect(line.map((i) => [i.key, i.text, i.direction])).toEqual([
      ["duration", "+2.5 s", "up"],
      ["cost", expect.stringMatching(/^−\$/), "down"],
      ["calls", "+3", "up"],
      ["retries", "—", "unknown"],
      ["files", "0", "same"],
    ]);
  });

  it("describes a failed step and clips a long command", () => {
    const step = {
      event_id: "e",
      event_type: "shell.command.failed",
      family: "shell",
      label: "x".repeat(200),
      label_withheld: false,
      status: "ERROR",
      duration_ms: null,
      offset_ms: null,
      span_id: null,
    };
    const parts = describeStep(step);
    expect(parts.at(-1)).toEqual({ text: " (failed)" });
    expect(parts.find((p) => p.code)!.text.length).toBeLessThanOrEqual(60);
  });

  it("writes scores and score changes", () => {
    expect(scoreText(null)).toBe("—");
    expect(scoreText({ score: 1, label: "pass" })).toBe("1 · pass");
    expect(deltaText(-0.5)).toBe("−0.5");
    expect(deltaText(0)).toBe("no change");
    expect(deltaText(null)).toBe("—");
  });
});
