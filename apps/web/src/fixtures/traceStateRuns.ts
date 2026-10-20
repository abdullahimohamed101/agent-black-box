import { FIXTURE_EPOCH, RunBuilder, type FixtureRun } from "./builder";

/**
 * Two runs whose trace the retention policy removed (Phase 19b, ADR-160): one finished (`expired`: the detail is gone and
 * only the record remains) and one the job is still removing (`deleting`). Both start before every other fixture run, so
 * the runs list keeps its order. Their summaries keep the numbers of the run they were; events, spans and findings are
 * gone, as the API serves them.
 */
export const EXPIRED_RUN_N = 90;
export const DELETING_RUN_N = 91;
export const EXPIRED_RUN_ID = new RunBuilder(EXPIRED_RUN_N, "", "", 0).runId;
export const DELETING_RUN_ID = new RunBuilder(DELETING_RUN_N, "", "", 0).runId;
const MIN = 60_000;
/** The agent of both runs: `/runs?agent=report-agent` lists them (the default list does not). */
export const TRACE_STATE_AGENT = "report-agent";

function small(n: number, name: string, startMin: number): FixtureRun {
  const b = new RunBuilder(n, name, TRACE_STATE_AGENT, FIXTURE_EPOCH - startMin * MIN);
  b.add("run.started", { "run.name": name }, 0);
  b.add("agent.started", {}, 100, { span_id: b.span(1) });
  b.add("tool.call.started", { "tool.name": "query_warehouse" }, 200, {
    span_id: b.span(2),
    parent_span_id: b.span(1),
  });
  b.add("tool.call.completed", { "tool.name": "query_warehouse" }, 900, {
    span_id: b.span(2),
    parent_span_id: b.span(1),
    status: "success",
    duration_ms: 900,
  });
  b.add(
    "llm.request.completed",
    {
      "llm.provider": "demo",
      "llm.model": "model-x",
      "llm.input_tokens": 1200,
      "llm.output_tokens": 180,
      "cost.estimated_usd": 0.0094,
    },
    1500,
    { span_id: b.span(3), parent_span_id: b.span(1), status: "success", duration_ms: 1500 },
  );
  b.add("agent.completed", {}, 100, { span_id: b.span(1), status: "success" });
  b.add("run.completed", {}, 100, { status: "success" });
  return b.build();
}

export function traceStateRuns(): FixtureRun[] {
  return [
    { f: small(EXPIRED_RUN_N, "Nightly revenue report", 3000), state: "expired" as const },
    { f: small(DELETING_RUN_N, "Weekly dependency audit", 2900), state: "deleting" as const },
  ].map(({ f, state }) => ({
    ...f,
    run: { ...f.run, trace_state: state },
    events: [],
    spans: [],
  }));
}
