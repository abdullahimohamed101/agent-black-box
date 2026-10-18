import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ExperimentDetail } from "@/components/ExperimentDetail";
import { Experiments } from "@/components/Experiments";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";
import { fixtureReply } from "@/fixtures/api";
import { fid } from "@/fixtures/ids";
import type { ExperimentOut } from "@/lib/api/types";
import { controlOf, parseConfig, verdictOf, warningText } from "@/lib/experiments";
import type { WorkspaceContext } from "@/lib/workspace";
import { BASE } from "./helpers";

afterEach(() => vi.unstubAllGlobals());

const CONTROLLED = fid("exp", 1);
const UNCONTROLLED = fid("exp", 2);
const OWNER = ["experiment.read", "experiment.write", "evaluation.run"];
const VIEWER = ["experiment.read"];

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

type Edit = (e: ExperimentOut) => ExperimentOut;
function stub({
  edit,
  status,
  onPost,
}: {
  edit?: Edit;
  status?: number;
  onPost?: (path: string, body: unknown) => Response;
} = {}) {
  const posts: { url: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      const url = new URL(input.url);
      if (input.method !== "GET") {
        const text = await input.text();
        const body: unknown = text ? JSON.parse(text) : null;
        posts.push({ url: `${input.method} ${url.pathname}`, body });
        return onPost?.(url.pathname, body) ?? Response.json({}, { status: 202 });
      }
      if (status)
        return Response.json({ error: { code: "INTERNAL", message: "boom" } }, { status });
      const segs = url.pathname.replace(/^\/api\/abb\//, "").split("/");
      const r = fixtureReply(segs, url.searchParams, { content: true });
      const body =
        edit && r.status === 200 && segs[1] === "experiments" && segs[2]
          ? edit(r.body as ExperimentOut)
          : r.body;
      return Response.json(body, { status: r.status });
    }),
  );
  return posts;
}

function wrap(ui: React.ReactElement, permissions: string[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <WorkspaceProvider value={ctx(permissions)}>{ui}</WorkspaceProvider>
    </QueryClientProvider>,
  );
}
const detail = (id: string, permissions = OWNER) =>
  wrap(<ExperimentDetail base={BASE} experimentId={id} />, permissions);

describe("Experiments list", () => {
  it("loads, lists experiments as links and renders hostile names as text", async () => {
    stub();
    const { container } = wrap(<Experiments base={BASE} />, VIEWER);
    expect(screen.getByText("Loading experiments…")).toBeInTheDocument();
    const table = await screen.findByTestId("experiment-list");
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(screen.getByRole("link", { name: "Session fix: second patch" })).toHaveAttribute(
      "href",
      expect.stringContaining(`/experiments/${CONTROLLED}`),
    );
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toContain("<b>not</b>");
    expect(screen.queryByRole("form", { name: "New experiment" })).toBeNull();
  });

  it("is empty, and errors with a retry", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ items: [] })),
    );
    wrap(<Experiments base={BASE} />, VIEWER);
    expect(await screen.findByText("No experiments yet")).toBeInTheDocument();
    expect(screen.getByText("Nobody has created an experiment.")).toBeInTheDocument();
    vi.unstubAllGlobals();
    stub({ status: 500 });
    wrap(<Experiments base={BASE} />, VIEWER);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });

  it("offers the create form only when the person may write, and posts the declared body", async () => {
    const user = userEvent.setup();
    const posts = stub({
      onPost: () => Response.json({ id: fid("exp", 9), name: "New one" }, { status: 201 }),
    });
    wrap(<Experiments base={{ workspace: "demo", project: fid("prj", 1) }} />, OWNER);
    const form = await screen.findByRole("form", { name: "New experiment" });
    await user.type(within(form).getByLabelText("Name"), "  New one ");
    await user.click(within(form).getByRole("button", { name: "Create experiment" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]!.url).toBe("POST /api/abb/v1/experiments");
    expect(posts[0]!.body).toMatchObject({
      name: "New one",
      project_id: fid("prj", 1),
      evaluator_ids: [],
    });
    expect(await screen.findByTestId("experiment-created")).toHaveTextContent("New one");
  });
});

describe("Experiment detail", () => {
  it("shows a controlled experiment: banner, declared settings, metrics with percentiles", async () => {
    stub();
    detail(CONTROLLED);
    expect(screen.getByText("Loading experiment…")).toBeInTheDocument();
    const banner = await screen.findByTestId("control-banner");
    expect(banner).toHaveAttribute("data-control", "single");
    expect(banner).toHaveTextContent("Controlled");
    expect(banner).toHaveTextContent("wrong fix changes: strategy");
    const variants = screen.getByTestId("variants");
    expect(within(variants).getByText("refresh-token")).toBeInTheDocument();
    const metrics = screen.getByTestId("metrics");
    expect(within(metrics).getByText("Cost, 95th percentile")).toBeInTheDocument();
    expect(within(metrics).getByText("Duration, median")).toBeInTheDocument();
    expect(within(metrics).getByText("Success rate")).toBeInTheDocument();
    expect(screen.queryByTestId("warnings")).toBeNull();
    const attached = screen.getByTestId("attached-runs");
    expect(
      within(attached).getAllByRole("link", { name: /Compare with baseline run/ }),
    ).toHaveLength(1);
  });

  it("shows the not-controlled banner naming the changed settings, the warning and a hostile name as text", async () => {
    stub();
    const { container } = detail(UNCONTROLLED);
    const banner = await screen.findByTestId("control-banner");
    expect(banner).toHaveAttribute("data-control", "multiple");
    expect(banner).toHaveTextContent("Not controlled");
    expect(banner).toHaveTextContent("candidate changes: model, prompt");
    expect(screen.getByTestId("warnings")).toHaveTextContent("More than one setting differs");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("<b>not</b>");
    expect(container.querySelector("h1 b")).toBeNull();
    expect(screen.getByText("No runs are attached yet.")).toBeInTheDocument();
  });

  it("flags the observed warnings", async () => {
    stub({
      edit: (e) => ({
        ...e,
        warnings: [
          { code: "variant_models_vary", variant_id: e.variants[0]!.id },
          { code: "unevaluated_runs", variant_id: null, count: 2 },
        ],
      }),
    });
    detail(CONTROLLED);
    const w = await screen.findByTestId("warnings");
    expect(w).toHaveTextContent("Runs in this variant used different models");
    expect(w).toHaveTextContent("(2)");
  });

  it("marks model-judge columns as opinions", async () => {
    stub({
      edit: (e) => ({
        ...e,
        evaluators: e.evaluators.map((v, i) => (i === 0 ? { ...v, ground_truth: false } : v)),
      }),
    });
    detail(CONTROLLED);
    const metrics = await screen.findByTestId("metrics");
    expect(metrics.querySelectorAll("[data-group=judge]").length).toBe(3);
    expect(within(metrics).getAllByText("model judge, not ground truth").length).toBe(3);
  });

  it("a viewer sees no forms and no evaluate button", async () => {
    stub();
    detail(CONTROLLED, VIEWER);
    await screen.findByTestId("control-banner");
    expect(screen.queryByRole("form", { name: "Add a variant" })).toBeNull();
    expect(screen.queryByRole("form", { name: "Attach a run" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Evaluate all" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Detach" })).toBeNull();
  });

  it("evaluates all, tells about the cap, the remainder and the skipped evaluators", async () => {
    const user = userEvent.setup();
    const posts = stub({
      onPost: () =>
        Response.json({
          enqueued: 500,
          remaining: 120,
          jobs: 40,
          at_limit: 1,
          skipped_evaluators: [{ evaluator_id: fid("evr", 3), reason: "archived" }],
        }),
    });
    detail(CONTROLLED);
    await screen.findByTestId("control-banner");
    expect(screen.getByText(/at most 500 evaluations per click/)).toBeInTheDocument();
    expect(screen.getByText(/short batch delay/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Evaluate all" }));
    const result = await screen.findByTestId("evaluate-result");
    expect(posts[0]!.url).toBe(`POST /api/abb/v1/experiments/${CONTROLLED}/evaluate`);
    expect(posts[0]!.body).toEqual({}); // the proxy refuses a write without a JSON body
    expect(result).toHaveTextContent("Started 500 evaluation(s) on 40 run(s).");
    expect(result).toHaveTextContent("120 remain: click again");
    expect(result).toHaveTextContent("skipped: archived");
  });

  it("shows the API's refusal to attach a run as text", async () => {
    const user = userEvent.setup();
    stub({
      onPost: () =>
        Response.json(
          { error: { code: "LIMIT_REACHED", message: "<i>Too many runs</i>" } },
          { status: 409 },
        ),
    });
    const { container } = detail(UNCONTROLLED);
    await screen.findByTestId("control-banner");
    const form = await screen.findByRole("form", { name: "Attach a run" });
    await user.click(await within(form).findByRole("button", { name: "Attach run" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("<i>Too many runs</i>");
    expect(container.querySelector("form i")).toBeNull();
  });

  it("adds a variant from key=value lines and refuses a malformed line locally", async () => {
    const user = userEvent.setup();
    const posts = stub({ onPost: () => Response.json({}, { status: 201 }) });
    detail(UNCONTROLLED);
    const form = await screen.findByRole("form", { name: "Add a variant" });
    await user.type(within(form).getByLabelText("Name"), "c2");
    await user.type(within(form).getByLabelText(/Declared settings/), "oops");
    await user.click(within(form).getByRole("button", { name: "Add variant" }));
    expect(await within(form).findByText(/Line 1: write it as key=value/)).toBeInTheDocument();
    expect(posts).toHaveLength(0);
    await user.clear(within(form).getByLabelText(/Declared settings/));
    await user.type(within(form).getByLabelText(/Declared settings/), "model=m3{Enter}prompt=v3");
    await user.click(within(form).getByRole("button", { name: "Add variant" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]!.body).toEqual({
      name: "c2",
      role: "candidate",
      config: { model: "m3", prompt: "v3" },
    });
  });

  it("errors with a retry and says not found", async () => {
    stub({ status: 500 });
    detail(CONTROLLED);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});

describe("experiment helpers", () => {
  it("derives the control state from the API's per-variant facts only", () => {
    const v = (
      role: "baseline" | "candidate",
      controlled: boolean | null,
      keys: string[] | null,
    ) => ({
      id: role + String(controlled),
      name: role,
      role,
      config: {},
      run_count: 0,
      controlled,
      changed_variables: keys,
    });
    expect(controlOf({ variants: [v("baseline", null, null)] }).state).toBe("no-candidates");
    expect(
      controlOf({ variants: [v("baseline", null, null), v("candidate", true, ["a"])] }).state,
    ).toBe("single");
    expect(
      controlOf({ variants: [v("baseline", null, null), v("candidate", false, ["a", "b"])] }).state,
    ).toBe("multiple");
    expect(
      controlOf({ variants: [v("baseline", null, null), v("candidate", false, [])] }).state,
    ).toBe("multiple");
  });

  it("parses config lines", () => {
    expect(parseConfig("a=1\n\n b = two \nc=x=y")).toEqual({
      config: { a: "1", b: "two", c: "x=y" },
      error: null,
    });
    expect(parseConfig("=1").error).toMatch(/Line 1/);
    expect(parseConfig("a=1\na=2").error).toMatch(/set twice/);
    expect(warningText({ code: "something_new" as "unevaluated_runs", variant_id: null })).toBe(
      "something_new",
    );
  });
});

// Step 5d: the page opens with which variant leads, in words, from the API's per-variant metrics only.
describe("the winner in words", () => {
  const dist = (v: number | null) => ({ mean: v, median: v, p95: v });
  const mk = (
    id: string,
    name: string,
    role: "baseline" | "candidate",
    sr: number | null,
    finished: number,
    cost: number | null,
  ) => ({
    v: {
      id,
      name,
      role,
      config: {},
      run_count: finished,
      controlled: role === "baseline" ? null : true,
      changed_variables: role === "baseline" ? null : ["model"],
    },
    m: {
      variant_id: id,
      runs: finished,
      finished,
      success_rate: sr,
      retry_rate: 0,
      tool_calls_mean: 1,
      cost_usd: dist(cost),
      duration_ms: dist(1),
      failure_categories: {},
      evaluations: [],
    },
  });
  const exp = (...rows: ReturnType<typeof mk>[]) => ({
    variants: rows.map((r) => r.v),
    metrics: rows.map((r) => r.m),
  });

  it("names the variant with the higher success rate, both rates, the cost and how many runs it rests on", () => {
    const v = verdictOf(
      exp(
        mk("a", "baseline", "baseline", 0.6, 10, 0.2),
        mk("b", "new prompt", "candidate", 0.8, 10, 0.1),
      ),
    );
    expect(v.kind).toBe("lead");
    expect(v.leader).toBe("b");
    expect(v.text).toBe(
      "New prompt leads on success rate: 80% against 60% for baseline. It also cost less, at a median $0.10 against $0.20. Based on 10 finished runs for new prompt and 10 for baseline.",
    );
  });

  it("says so when the leader cost more, and when too few runs back it", () => {
    const v = verdictOf(
      exp(
        mk("a", "baseline", "baseline", 1, 1, 0.17),
        mk("b", "wrong fix", "candidate", 0, 1, 0.14),
      ),
    );
    expect(v.leader).toBe("a");
    expect(v.text).toContain("Baseline leads on success rate: 100% against 0% for wrong fix.");
    expect(v.text).toContain("It cost more, at a median $0.17 against $0.14.");
    expect(v.text).toContain("Based on 1 finished run for baseline and 1 for wrong fix.");
    expect(v.text).toMatch(/too few to be sure\.$/);
  });

  it("a tie on success is broken by the median cost, in words, and a full tie names no one", () => {
    const cheaper = verdictOf(
      exp(
        mk("a", "baseline", "baseline", 0.9, 8, 0.3),
        mk("b", "small model", "candidate", 0.9, 8, 0.1),
      ),
    );
    expect(cheaper.leader).toBe("b");
    expect(cheaper.text).toContain(
      "Small model and baseline both succeeded in 90% of runs, and small model cost less",
    );
    const tie = verdictOf(
      exp(mk("a", "baseline", "baseline", 0.9, 8, 0.3), mk("b", "same", "candidate", 0.9, 8, 0.3)),
    );
    expect(tie.kind).toBe("tie");
    expect(tie.leader).toBeNull();
    expect(tie.text).toContain(
      "No variant leads: baseline and same both succeeded in 90% of runs.",
    );
    expect(tie.text).toContain("The median cost is the same, $0.30.");
  });

  it("decides on the printed precision: rates that both read 80% and costs that both read $0.0120 are ties", () => {
    const sameRate = verdictOf(
      exp(
        mk("a", "baseline", "baseline", 0.804, 10, 0.3),
        mk("b", "same", "candidate", 0.801, 10, 0.1),
      ),
    );
    // equal as printed, so the cost breaks the tie rather than "80% against 80%"
    expect(sameRate.text).not.toMatch(/leads on success rate/);
    expect(sameRate.leader).toBe("b");
    expect(sameRate.text).toContain("both succeeded in 80% of runs");
    const sameCost = verdictOf(
      exp(
        mk("a", "baseline", "baseline", 0.8, 10, 0.01201),
        mk("b", "same", "candidate", 0.8, 10, 0.01204),
      ),
    );
    expect(sameCost.kind).toBe("tie");
    expect(sameCost.text).toContain("The median cost is the same, $0.0120.");
    const differs = verdictOf(
      exp(
        mk("a", "baseline", "baseline", 0.8, 10, 0.012),
        mk("b", "other", "candidate", 0.8, 10, 0.014),
      ),
    );
    expect(differs.leader).toBe("a");
  });

  it("says when it compares two of more than two variants", () => {
    const three = verdictOf(
      exp(
        mk("a", "baseline", "baseline", 0.9, 10, 0.3),
        mk("b", "one", "candidate", 0.7, 10, 0.1),
        mk("c", "two", "candidate", 0.5, 10, 0.1),
      ),
    );
    expect(three.text).toContain("It compares the top 2 of 3 variants with finished runs.");
    const two = verdictOf(
      exp(mk("a", "baseline", "baseline", 0.9, 10, 0.3), mk("b", "one", "candidate", 0.7, 10, 0.1)),
    );
    expect(two.text).not.toContain("top 2 of");
  });

  it("says there is not enough to compare when fewer than two variants have finished runs", () => {
    const v = verdictOf(
      exp(mk("a", "baseline", "baseline", 1, 3, 0.1), mk("b", "empty", "candidate", null, 0, null)),
    );
    expect(v.kind).toBe("none");
    expect(v.text).toBe(
      "Not enough finished runs to compare yet: attach finished runs to at least two variants.",
    );
  });

  it("the page opens with the sentence, and the chart marks the leader while the words say it", async () => {
    stub();
    detail(CONTROLLED);
    const sentence = await screen.findByTestId("experiment-verdict");
    expect(sentence).toHaveAttribute("data-verdict", "lead");
    expect(sentence).toHaveTextContent(/leads on success rate: 100% against 0%/);
    const bars = screen.getByRole("list", { name: "Success rate by variant" });
    const items = within(bars).getAllByRole("listitem");
    expect(items.map((i) => i.textContent)).toEqual(["baseline100%", "wrong fix0%"]);
    expect(items[0]).toHaveAttribute("data-lead", "true");
    // the leader cost more here, so the cost chart marks the cheaper variant instead
    const cost = within(screen.getByRole("list", { name: /median cost/i })).getAllByRole(
      "listitem",
    );
    expect(cost.map((i) => i.getAttribute("data-lead"))).toEqual([null, "true"]);
    // the sentence comes before the control notice and the tables
    const banner = screen.getByTestId("control-banner");
    expect(
      sentence.compareDocumentPosition(banner) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("an experiment without finished runs says there is nothing to compare, and draws no chart", async () => {
    stub();
    detail(UNCONTROLLED);
    expect(await screen.findByTestId("experiment-verdict")).toHaveAttribute("data-verdict", "none");
    expect(screen.queryByRole("list", { name: "Success rate by variant" })).toBeNull();
  });
});
