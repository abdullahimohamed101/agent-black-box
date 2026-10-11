import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fixtureRuns } from "@/fixtures";
import {
  fixtureComparison,
  fixtureEvaluators,
  fixtureExperiments,
  fixtureRunEvaluations,
} from "@/fixtures/evaluations";
import { BASELINE_RUN_N, WRONG_FIX_RUN_N } from "@/fixtures/codingRuns";
import { fid } from "@/fixtures/ids";
import {
  useComparison,
  useEvaluators,
  useExperiment,
  useExperiments,
  useRecordVerdict,
  useRunEvaluations,
  useTriggerEvaluation,
} from "@/lib/queries";
import type { EvaluationOut } from "@/lib/api/types";
import { stubApi } from "./helpers";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const BASE_ID = fid("run", BASELINE_RUN_N);
const WRONG_ID = fid("run", WRONG_FIX_RUN_N);
const runOf = (id: string) => fixtureRuns().find((f) => f.run.id === id)!;

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const Wrap = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { Wrap, client };
}
const row = (state: EvaluationOut["state"]) =>
  ({ ...fixtureRunEvaluations(runOf(BASE_ID), { content: true }).items[0]!, state }) as const;

describe("fixture data: the states and the projections", () => {
  it("the wrong-fix run shows every evaluation state", () => {
    const states = new Set(
      fixtureRunEvaluations(runOf(WRONG_ID), { content: true }).items.map((e) => e.state),
    );
    expect(states).toEqual(new Set(["pending", "running", "completed", "failed"]));
  });

  it("the passing test run is the evidence of the baseline's tests-pass result", () => {
    const f = runOf(BASE_ID);
    const row = fixtureRunEvaluations(f, { content: true }).items.find(
      (e) => e.evaluator_name === "tests-pass",
    )!;
    const cited = f.events.find((e) => e.event_id === row.evidence_event_ids[0])!;
    expect(cited.event_type).toBe("shell.command.completed");
    expect(cited.attributes["test.failed"]).toBe(0);
  });

  it("a viewer keeps scores and labels and loses free text, rubric, globs and raw metadata", () => {
    const owner = fixtureRunEvaluations(runOf(BASE_ID), { content: true }).items;
    const viewer = fixtureRunEvaluations(runOf(BASE_ID), { content: false }).items;
    expect(viewer.map((e) => [e.score, e.label])).toEqual(owner.map((e) => [e.score, e.label]));
    const human = viewer.find((e) => e.kind === "human")!;
    expect(human.explanation).toBeNull();
    expect(human.explanation_withheld).toBe(true);
    const judge = viewer.find((e) => e.kind === "llm_judge")!;
    expect(judge.ground_truth).toBe(false);
    expect(judge.metadata).not.toHaveProperty("raw");
    expect(judge.metadata_withheld).toContain("raw");
    expect(JSON.stringify(viewer)).not.toContain("onerror");
    const failed = fixtureRunEvaluations(runOf(WRONG_ID), { content: false }).items.find(
      (e) => e.state === "failed",
    )!;
    expect(failed.error).toBeNull();
    expect(failed.error_code).toBe("TIMEOUT");

    const evs = fixtureEvaluators({ content: false });
    const files = evs.find((e) => e.implementation === "files_touched_only")!;
    expect(files.config_withheld.sort()).toEqual(["allowed_globs", "forbidden_globs"]);
    expect(files.config).toEqual({ allowed_globs: null, forbidden_globs: null });
    expect(evs.find((e) => e.kind === "llm_judge")!.config["rubric"]).toBeNull();
    expect(evs.find((e) => e.implementation === "cost_under")!.config).toEqual({ max_usd: 1 });
  });

  it("the demo pair diverges at the second edit and differs in outcome at the second test run", () => {
    const owner = fixtureComparison(runOf(BASE_ID), runOf(WRONG_ID), { content: true });
    expect(owner.alignment).toBe("content");
    const at = owner.steps[owner.divergence!.index]!;
    expect([at.left?.label, at.right?.label]).toEqual(["app/session.py", "app/oauth.py"]);
    const outcome = owner.steps[owner.first_outcome_difference!]!;
    expect(outcome.left?.status).toBe("success");
    expect(outcome.right?.status).toBe("error");
    expect(owner.files.both).toEqual(["app/session.py"]);
    expect(owner.files.only_right).toEqual(["app/oauth.py"]);

    const viewer = fixtureComparison(runOf(BASE_ID), runOf(WRONG_ID), { content: false });
    expect(viewer.alignment).toBe("metadata");
    expect(viewer.files.paths_withheld).toBe(true);
    expect(viewer.files.both).toEqual([]);
    expect(viewer.files.left_count).toBe(1);
    const text = JSON.stringify(viewer);
    for (const secret of ["app/session.py", "app/oauth.py", "unittest", "tail -n"])
      expect(text).not.toContain(secret);
    // the divergence of the metadata projection is the second test run, as in the API
    expect(viewer.divergence!.index).toBe(viewer.first_outcome_difference);
  });

  it("an experiment reports whether it is controlled, with the variables that changed", () => {
    const [controlled, mixed] = fixtureExperiments(fixtureRuns(), { content: false });
    expect(controlled!.variants.map((v) => v.controlled)).toEqual([null, true]);
    expect(controlled!.warnings).toEqual([]);
    expect(mixed!.variants[1]!.changed_variables).toEqual(["model", "prompt"]);
    expect(mixed!.warnings[0]!.code).toBe("not_single_variable");
  });
});

describe("hooks", () => {
  it("useRunEvaluations polls while a row is pending or running and stops when none is", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let n = 0;
    const calls = stubApi({
      "/evaluations": () => {
        n += 1;
        const state = n < 3 ? (n === 1 ? "pending" : "running") : "completed";
        return Response.json({ items: [row(state)], truncated: false });
      },
    });
    const { Wrap } = wrapper();
    const { result } = renderHook(() => useRunEvaluations(BASE_ID), { wrapper: Wrap });
    await waitFor(() => expect(result.current.data?.items[0]?.state).toBe("pending"));
    await act(() => vi.advanceTimersByTimeAsync(3100));
    await waitFor(() => expect(result.current.data?.items[0]?.state).toBe("running"));
    await act(() => vi.advanceTimersByTimeAsync(3100));
    await waitFor(() => expect(result.current.data?.items[0]?.state).toBe("completed"));
    const settled = calls.filter((c) => c.includes("/evaluations")).length;
    await act(() => vi.advanceTimersByTimeAsync(20_000));
    expect(calls.filter((c) => c.includes("/evaluations")).length).toBe(settled); // no polling any more
  });

  it("useComparison sends the right run as with_run_id and waits for both ids", async () => {
    const calls = stubApi();
    const { Wrap } = wrapper();
    const idle = renderHook(() => useComparison(BASE_ID, null), { wrapper: Wrap });
    expect(idle.result.current.fetchStatus).toBe("idle");
    const { result } = renderHook(() => useComparison(BASE_ID, WRONG_ID), { wrapper: Wrap });
    await waitFor(() => expect(result.current.data?.right.run_id).toBe(WRONG_ID));
    expect(calls.some((c) => c.includes(`/runs/${BASE_ID}/compare?with_run_id=${WRONG_ID}`))).toBe(
      true,
    );
  });

  it("lists evaluators and experiments through the typed client", async () => {
    stubApi();
    const { Wrap } = wrapper();
    const evs = renderHook(() => useEvaluators(), { wrapper: Wrap });
    const exps = renderHook(() => useExperiments(), { wrapper: Wrap });
    const one = renderHook(
      () => useExperiment(fixtureExperiments(fixtureRuns(), { content: true })[0]!.id),
      {
        wrapper: Wrap,
      },
    );
    await waitFor(() => expect(evs.result.current.data?.length).toBe(5));
    await waitFor(() => expect(exps.result.current.data?.length).toBe(2));
    await waitFor(() => expect(one.result.current.data?.variants).toHaveLength(2));
    // hostile text is data: it stays a string with its markup intact
    expect(exps.result.current.data!.some((e) => e.name.includes("<b>not</b>"))).toBe(true);
  });

  it("the trigger and the verdict post the documented bodies and refresh the run's evaluations", async () => {
    const seen: { url: string; body: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request) => {
        const url = new URL(input.url);
        if (input.method === "POST") {
          seen.push({ url: url.pathname, body: await input.json() });
          return Response.json(row("pending"), { status: 202 });
        }
        seen.push({ url: `GET ${url.pathname}`, body: null });
        return Response.json({ items: [], truncated: false });
      }),
    );
    const { Wrap, client } = wrapper();
    const evals = renderHook(() => useRunEvaluations(BASE_ID), { wrapper: Wrap });
    await waitFor(() => expect(evals.result.current.isSuccess).toBe(true));
    const trigger = renderHook(() => useTriggerEvaluation(BASE_ID), { wrapper: Wrap });
    const verdict = renderHook(() => useRecordVerdict(BASE_ID), { wrapper: Wrap });
    await act(() => trigger.result.current.mutateAsync("evr_x"));
    await act(() =>
      verdict.result.current.mutateAsync({ evaluator_id: "evr_h", label: "pass", score: 1 }),
    );
    expect(seen.filter((s) => !s.url.startsWith("GET"))).toEqual([
      { url: `/api/abb/v1/runs/${BASE_ID}/evaluations`, body: { evaluator_id: "evr_x" } },
      {
        url: `/api/abb/v1/runs/${BASE_ID}/verdicts`,
        body: { evaluator_id: "evr_h", label: "pass", score: 1 },
      },
    ]);
    // each write refetched the list
    await waitFor(() =>
      expect(
        seen.filter((s) => s.url === `GET /api/abb/v1/runs/${BASE_ID}/evaluations`).length,
      ).toBeGreaterThan(1),
    );
    client.clear();
  });

  it("a refused write reaches the caller as the API's code (viewer 403)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { error: { code: "PERMISSION_DENIED", message: "No.", request_id: "r1" } },
          { status: 403 },
        ),
      ),
    );
    const { Wrap } = wrapper();
    const trigger = renderHook(() => useTriggerEvaluation(BASE_ID), { wrapper: Wrap });
    await expect(act(() => trigger.result.current.mutateAsync("evr_x"))).rejects.toMatchObject({
      status: 403,
      code: "PERMISSION_DENIED",
    });
  });
});
