import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Evaluations } from "@/components/Evaluations";
import { DEFAULT_FILTERS, RunsList, type ListFilters } from "@/components/RunsList";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";
import { BASELINE_RUN_N, WRONG_FIX_RUN_N } from "@/fixtures/codingRuns";
import { fixtureReply } from "@/fixtures/api";
import { fixtureRuns } from "@/fixtures";
import { fid } from "@/fixtures/ids";
import { COMPARE_PICKS, compareHref, toggleRun } from "@/lib/compare";
import { formatScore, labelTone } from "@/lib/evaluations";
import type { WorkspaceContext } from "@/lib/workspace";
import { BASE } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

const BASELINE = fid("run", BASELINE_RUN_N);
const WRONG = fid("run", WRONG_FIX_RUN_N);
const OWNER = ["evaluation.read", "evaluation.run", "evaluation.label", "evaluator.write"];
const VIEWER = ["evaluation.read"];

function ctx(permissions: string[]): WorkspaceContext {
  return {
    mode: "session",
    user: null,
    workspace: { id: "ws_00000000000000000000000001", slug: "demo", name: "Demo" },
    role: null,
    permissions,
    ownPermissions: [],
    memberships: [],
    projects: [],
  };
}

type Posts = { url: string; body: unknown }[];
/** Fixture-backed fetch; POSTs are recorded and answered by `onPost`; `override` can replace a GET by path suffix. */
function stub({
  content = true,
  onPost,
  override = {},
}: {
  content?: boolean;
  onPost?: (url: string, body: unknown) => Response;
  override?: Record<string, () => Response>;
} = {}) {
  const posts: Posts = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const url = new URL(input.url);
      if (input.method === "POST") {
        const body: unknown = await input.json();
        posts.push({ url: url.pathname, body });
        return onPost?.(url.pathname, body) ?? Response.json({}, { status: 202 });
      }
      const hit = Object.keys(override).find((k) => url.pathname.endsWith(k));
      if (hit) return override[hit]!();
      const segs = url.pathname.replace(/^\/api\/abb\//, "").split("/");
      const r = fixtureReply(segs, url.searchParams, { content });
      return Response.json(r.body, { status: r.status });
    }),
  );
  return posts;
}

function show(runId: string, permissions: string[], onShowEvent?: (id: string) => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <WorkspaceProvider value={ctx(permissions)}>
        <Evaluations runId={runId} onShowEvent={onShowEvent} />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
}

describe("Evaluations", () => {
  it("shows the loading state first, then the latest result of each evaluator", async () => {
    stub();
    show(BASELINE, OWNER);
    expect(screen.getByText("Loading evaluations…")).toBeInTheDocument();
    const rows = await screen.findAllByTestId("evaluation");
    expect(rows).toHaveLength(4);
    const tests = rows.find((r) => within(r).queryByRole("heading", { name: "tests-pass" }))!;
    expect(tests).toHaveAttribute("data-state", "completed");
    expect(within(tests).getByTestId("evaluation-label")).toHaveTextContent("pass");
    expect(within(tests).getByText("1.0")).toBeInTheDocument();
  });

  it("marks a model judge as an opinion, once, and no other kind", async () => {
    stub();
    show(BASELINE, OWNER);
    await screen.findAllByTestId("evaluation");
    const badges = screen.getAllByTestId("judge-badge");
    expect(badges).toHaveLength(1);
    expect(badges[0]).toHaveTextContent("model judge, not ground truth");
    expect(badges[0]!.closest("[data-testid=evaluation]")).toHaveAttribute(
      "data-kind",
      "llm_judge",
    );
    expect(screen.getByText(/Judge model/)).toBeInTheDocument();
    expect(screen.getByText("fake-judge-1")).toBeInTheDocument();
  });

  it("renders a hostile explanation as text, never as markup", async () => {
    stub();
    const { container } = show(BASELINE, OWNER);
    await screen.findAllByTestId("evaluation");
    const text = screen
      .getAllByTestId("explanation")
      .map((n) => n.textContent)
      .join("\n");
    expect(text).toContain('<img src=x onerror="alert(1)">');
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a[href^='javascript']")).toBeNull();
    expect(container.querySelector("strong")?.textContent).not.toBe("not bold");
  });

  it("shows pending, running, failed and completed rows, a live notice and the earlier results", async () => {
    stub();
    show(WRONG, OWNER);
    await screen.findAllByTestId("evaluation");
    const states = screen
      .getAllByTestId("evaluation")
      .map((r) => r.getAttribute("data-state"))
      .sort();
    expect(states).toEqual(["completed", "failed", "failed", "pending", "running"].sort());
    expect(screen.getByTestId("evaluations-live")).toBeInTheDocument();
    expect(screen.getAllByTestId("evaluation-error")[0]).toHaveTextContent("TIMEOUT");
    const history = screen.getByText(/Earlier results \(1\)/).closest("details")!;
    expect(within(history).getByTestId("evaluation-error")).toHaveTextContent("JOB_FAILED");
    // a failed row is words and a glyph, not colour alone
    expect(screen.getAllByText("Failed").length).toBeGreaterThan(0);
  });

  it("a viewer sees scores and labels but no free text, and no controls", async () => {
    stub({ content: false });
    const { container } = show(BASELINE, VIEWER);
    await screen.findAllByTestId("evaluation");
    expect(screen.getAllByTestId("evaluation-label").map((n) => n.textContent)).toContain("pass");
    expect(screen.getAllByTestId("explanation-withheld").length).toBe(2); // human and model judge
    expect(container.textContent).not.toContain("onerror");
    expect(container.textContent).not.toContain("refreshes early");
    expect(screen.getAllByTestId("metadata-withheld")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Run evaluator" })).toBeNull();
    expect(screen.queryByRole("form", { name: "Record a verdict" })).toBeNull();
  });

  it("states what is empty, per role", async () => {
    stub();
    const first = fixtureRuns().find((f) => f.run.name === "Fix login session expiry")!.run.id;
    show(first, OWNER);
    expect(await screen.findByText("No evaluations yet")).toBeInTheDocument();
    expect(screen.getByText(/Start an evaluator below/)).toBeInTheDocument();
  });

  it("a viewer's empty state does not offer to start anything", async () => {
    stub({ content: false });
    show(fixtureRuns()[0]!.run.id, VIEWER);
    expect(await screen.findByText("Nobody has evaluated this run.")).toBeInTheDocument();
  });

  it("shows the API's error with a retry, and a 403 as a plain role note", async () => {
    stub({
      override: {
        "/evaluations": () =>
          Response.json({ error: { code: "INTERNAL", message: "boom" } }, { status: 500 }),
      },
    });
    show(BASELINE, OWNER);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    vi.unstubAllGlobals();

    stub({
      override: {
        "/evaluations": () =>
          Response.json({ error: { code: "PERMISSION_DENIED", message: "no" } }, { status: 403 }),
      },
    });
    show(BASELINE, VIEWER);
    expect(await screen.findByText(/not available for your role/)).toBeInTheDocument();
  });

  it("says when older results were left out", async () => {
    stub({
      override: {
        "/evaluations": () => Response.json({ items: [], truncated: true }),
      },
    });
    show(BASELINE, OWNER);
    expect(await screen.findByTestId("evaluations-truncated")).toBeInTheDocument();
  });

  it("starts an evaluator with the chosen id and shows the API's refusal as text", async () => {
    const user = userEvent.setup();
    const posts = stub({
      onPost: () =>
        Response.json(
          { error: { code: "JUDGE_NOT_CONFIGURED", message: "No model judge is configured." } },
          { status: 409 },
        ),
    });
    show(BASELINE, OWNER);
    await screen.findAllByTestId("evaluation");
    const group = await screen.findByRole("group", { name: "Evaluator" });
    expect(within(group).queryByRole("radio", { name: /reviewer-verdict/ })).toBeNull(); // human: a verdict instead
    expect(within(group).getAllByRole("radio")).toHaveLength(4);
    await user.click(within(group).getByRole("radio", { name: /quality-judge/ }));
    await user.click(screen.getByRole("button", { name: "Run evaluator" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      url: `/api/abb/v1/runs/${BASELINE}/evaluations`,
      body: { evaluator_id: fid("evr", 4) },
    });
    expect(await screen.findByText(/No model judge is configured/)).toBeInTheDocument();
  });

  it("records a verdict: labels come from the evaluator, a bad score is refused before sending", async () => {
    const user = userEvent.setup();
    const posts = stub({ onPost: () => Response.json({}, { status: 202 }) });
    show(BASELINE, OWNER);
    const form = await screen.findByRole("form", { name: "Record a verdict" });
    const label = within(form).getByRole("group", { name: "Label" });
    expect(
      within(label)
        .getAllByRole("radio")
        .map((o) => (o as HTMLInputElement).value),
    ).toEqual(["pass", "fail", "unrated"]);
    await user.type(within(form).getByLabelText(/Score/), "2");
    await user.click(within(form).getByRole("button", { name: "Save verdict" }));
    expect(within(form).getByText("Enter a number from 0 to 1.")).toBeInTheDocument();
    expect(posts).toHaveLength(0);
    await user.clear(within(form).getByLabelText(/Score/));
    await user.type(within(form).getByLabelText(/Score/), "0.5");
    await user.click(within(label).getByRole("radio", { name: "fail" }));
    await user.type(within(form).getByLabelText(/Explanation/), "<b>flaky</b>");
    await user.click(within(form).getByRole("button", { name: "Save verdict" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({
      url: `/api/abb/v1/runs/${BASELINE}/verdicts`,
      body: {
        evaluator_id: fid("evr", 5),
        label: "fail",
        score: 0.5,
        explanation: "<b>flaky</b>",
      },
    });
    expect(await within(form).findByText("Verdict saved.")).toBeInTheDocument();
  });

  it("a refused verdict (a role the API denies) is a banner, not a crash", async () => {
    const user = userEvent.setup();
    stub({
      onPost: () =>
        Response.json(
          { error: { code: "PERMISSION_DENIED", message: "Not allowed." } },
          { status: 403 },
        ),
    });
    show(BASELINE, OWNER);
    const form = await screen.findByRole("form", { name: "Record a verdict" });
    await user.click(within(form).getByRole("button", { name: "Save verdict" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "Your role does not allow this.",
    );
  });

  it("Show evidence jumps to the first cited event", async () => {
    const user = userEvent.setup();
    stub({
      override: {
        "/evaluations": () =>
          Response.json({
            items: [
              {
                ...JSON.parse(
                  JSON.stringify(
                    fixtureReply(["v1", "runs", BASELINE, "evaluations"], new URLSearchParams())
                      .body as { items: unknown[] },
                  ),
                ).items[3],
                evidence_event_ids: ["evt_a", "evt_b"],
              },
            ],
            truncated: false,
          }),
      },
    });
    const jump = vi.fn();
    show(BASELINE, VIEWER, jump);
    await user.click(await screen.findByRole("button", { name: "Show evidence" }));
    expect(jump).toHaveBeenCalledWith("evt_a");
  });
});

describe("pure rules", () => {
  it("formats scores and label tones", () => {
    expect(formatScore(null)).toBe("—");
    expect(formatScore(1)).toBe("1.0");
    expect(formatScore(0.8333333)).toBe("0.833");
    expect(labelTone("pass")).toBe("ok");
    expect(labelTone("FAIL")).toBe("bad");
    expect(labelTone("unrated")).toBe("neutral");
    expect(labelTone(null)).toBe("neutral");
  });

  it("the compare selection holds two runs, refuses a third and links left then right", () => {
    expect(toggleRun([], "a")).toEqual(["a"]);
    expect(toggleRun(["a"], "b")).toEqual(["a", "b"]);
    expect(toggleRun(["a", "b"], "c")).toEqual(["a", "b"]);
    expect(toggleRun(["a", "b"], "a")).toEqual(["b"]);
    expect(compareHref(BASE, ["a"])).toBeNull();
    expect(compareHref(BASE, ["a", "b"])).toBe("/w/demo/projects/all/runs/compare?left=a&right=b");
    expect(COMPARE_PICKS).toBe(2);
  });
});

function Harness() {
  const [f, setF] = useState<ListFilters>(DEFAULT_FILTERS);
  return (
    <RunsList base={BASE} filters={f} onFilters={setF} now={Date.parse("2026-10-08T00:00:00Z")} />
  );
}

describe("compare selection in the runs list", () => {
  it("picks two runs, disables the rest, links to the compare route and clears", async () => {
    stub();
    const user = userEvent.setup();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    render(
      <QueryClientProvider client={client}>
        <Harness />
      </QueryClientProvider>,
    );
    // Phase 22 D8: the tick boxes and the bar appear only once the person starts comparing.
    await screen.findByRole("table", { name: "Runs" });
    expect(screen.queryByTestId("compare-bar")).toBeNull();
    expect(screen.queryAllByRole("checkbox", { name: /^Compare / })).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Compare" }));
    const bar = await screen.findByTestId("compare-bar");
    expect(bar).toHaveTextContent("Tick 2 runs to compare them.");
    expect(screen.queryByTestId("compare-link")).toBeNull();
    const boxes = screen.getAllByRole("checkbox", { name: /^Compare / });
    expect(boxes).toHaveLength(fixtureRuns().length);
    await user.click(boxes[0]!);
    expect(bar).toHaveTextContent("1 of 2 runs picked.");
    expect(screen.queryByTestId("compare-link")).toBeNull();
    await user.click(boxes[1]!);
    const link = screen.getByTestId("compare-link");
    expect(link.getAttribute("href")).toMatch(/\/runs\/compare\?left=run_\w+&right=run_\w+$/);
    expect(boxes[2]).toBeDisabled();
    expect(boxes[0]).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Clear picks" }));
    expect(boxes[2]).toBeEnabled();
    expect(screen.queryByTestId("compare-link")).toBeNull();
  });
});
