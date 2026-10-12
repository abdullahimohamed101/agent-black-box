import type { EventOut, RunFindingsOut, RunOut, SpanOut } from "@/lib/api/types";
import { orderingMode, sortEvents } from "@/lib/ordering";
import { fid } from "./ids";

export const FIXTURE_PROJECT = fid("prj", 1);
/** The summary version the fixtures claim: the one that carries first-error facts for every failed status (ADR-112). */
export const FIXTURE_SUMMARY_VERSION = 6;
export const FIXTURE_EPOCH = Date.parse("2026-10-07T09:00:00Z");

export type FixtureRun = {
  run: RunOut;
  events: EventOut[];
  spans: SpanOut[];
  /** What `GET /v1/runs/{id}/findings` returns; attached by `fixtureRuns()`. */
  findings?: RunFindingsOut;
};

type Extra = Partial<
  Pick<EventOut, "status" | "duration_ms" | "span_id" | "parent_span_id" | "payload" | "agent_id">
> & {
  /** A remote emitter (another process): no `sequence`, so the run is time-ordered (D4). */
  sequence?: null;
  /** The emitter's clock offset in ms: shifts `occurred_at` only, never `received_at` or the builder's clock. */
  atMs?: number;
};

/**
 * Builds one run's events and derives its summary like the API worker does. `build()` puts the events in canonical
 * order (`sortEvents`) and reports the `ordering_mode` that order implies, so a run with events from a remote emitter
 * is time-ordered exactly as the API would serve it.
 */
export class RunBuilder {
  readonly events: EventOut[] = [];
  private seq = 0;
  private t: number;
  private evtN: number;
  readonly runId: string;
  readonly traceId: string;

  constructor(
    readonly n: number,
    readonly name: string,
    readonly agent: string,
    startMs: number,
  ) {
    this.runId = fid("run", n);
    this.traceId = fid("trc", n);
    this.t = startMs;
    this.evtN = n * 1_000_000;
  }

  span(k: number): string {
    return fid("spn", this.n * 100_000 + k);
  }

  add(
    event_type: string,
    attributes: Record<string, unknown> = {},
    gapMs = 400,
    extra: Extra = {},
  ): EventOut {
    if (extra.sequence !== null) this.seq += 1;
    this.evtN += 1;
    this.t += gapMs;
    const at = new Date(this.t).toISOString();
    const occurred = extra.atMs ? new Date(this.t + extra.atMs).toISOString() : at;
    const e: EventOut = {
      schema_version: "1.0",
      event_id: fid("evt", this.evtN),
      workspace_id: "wsp_00000000000000000000000001",
      project_id: FIXTURE_PROJECT,
      run_id: this.runId,
      trace_id: this.traceId,
      span_id: extra.span_id ?? null,
      parent_span_id: extra.parent_span_id ?? null,
      agent_id: extra.agent_id ?? this.agent,
      agent_version: "1.4.2",
      event_type,
      occurred_at: occurred,
      received_at: at,
      sequence: extra.sequence === null ? null : this.seq,
      status: extra.status ?? null,
      duration_ms: extra.duration_ms ?? null,
      attributes,
      payload: extra.payload ?? null,
      payload_ref: null,
      has_payload: extra.payload != null,
      payload_withheld: false,
      tags: [],
      sdk: { name: "fixture", version: "0" },
    };
    this.events.push(e);
    return e;
  }

  build(): FixtureRun {
    const ev = sortEvents(this.events);
    const mode = orderingMode(ev);
    const first = ev[0]!;
    const last = ev[ev.length - 1]!;
    const sum = (t: string, a: string) =>
      ev.filter((e) => e.event_type === t).reduce((s, e) => s + Number(e.attributes[a] ?? 0), 0);
    const isErr = (e: EventOut) =>
      e.status === "error" || e.status === "timeout" || e.event_type.endsWith(".failed");
    const lastRun = [...ev].reverse().find((e) => e.event_type.startsWith("run."));
    const status: RunOut["status"] = (() => {
      switch (lastRun?.event_type) {
        case "run.completed":
          return lastRun.status === "error"
            ? "FAILED"
            : lastRun.status === "timeout"
              ? "TIMED_OUT"
              : lastRun.status === "blocked"
                ? "BLOCKED"
                : "SUCCESS";
        case "run.failed":
          return "FAILED";
        case "run.cancelled":
          return "CANCELLED";
        default:
          return ev.some((e) => e.event_type === "approval.requested") &&
            !ev.some((e) => e.event_type === "approval.granted")
            ? "WAITING_FOR_APPROVAL"
            : "RUNNING";
      }
    })();
    const terminal = !["RUNNING", "WAITING_FOR_APPROVAL"].includes(status);
    const duration = terminal ? Date.parse(last.occurred_at) - Date.parse(first.occurred_at) : null;
    const models = [...new Set(ev.map((e) => e.attributes["llm.model"]).filter(Boolean))];
    const files = new Set(
      ev
        .filter((e) => ["file.created", "file.modified", "file.deleted"].includes(e.event_type))
        .map((e) => String(e.attributes["file.path"])),
    );
    // The stored first-error facts (ADR-112), computed the way the worker does: "after" is canonical event order.
    // A blocked run with no error event takes its blocking event: the first policy block, else the blocked completion.
    const blockedAt = () => {
      const policy = ev.findIndex((e) => e.event_type === "policy.action.blocked");
      if (policy >= 0) return policy;
      for (let i = ev.length - 1; i >= 0; i--)
        if (ev[i]!.event_type === "run.completed" && ev[i]!.status === "blocked") return i;
      return -1;
    };
    const errAt =
      ev.findIndex(isErr) >= 0 ? ev.findIndex(isErr) : status === "BLOCKED" ? blockedAt() : -1;
    const firstErr = errAt >= 0 ? ev[errAt]! : null;
    const later = firstErr ? ev.slice(errAt + 1) : [];
    const calls = later.filter((e) => e.event_type === "llm.request.completed");
    const firstErrorOut: RunOut["first_error"] = firstErr
      ? {
          event_id: firstErr.event_id,
          sequence: firstErr.sequence ?? null,
          retries_after: later.filter((e) => e.event_type === "retry.attempted").length,
          seconds_after: Math.max(
            0,
            (Date.parse(last.occurred_at) - Date.parse(firstErr.occurred_at)) / 1000,
          ),
          cost_after_usd: Number(
            calls
              .reduce((s, e) => s + Number(e.attributes["cost.estimated_usd"] ?? 0), 0)
              .toFixed(6),
          ),
          cost_after_unpriced_calls: calls.filter((e) => e.attributes["cost.estimated_usd"] == null)
            .length,
        }
      : null;
    const run: RunOut = {
      id: this.runId,
      project_id: FIXTURE_PROJECT,
      name: this.name,
      status,
      agent_id: this.agent,
      trace_id: this.traceId,
      started_at: first.occurred_at,
      completed_at: terminal ? last.occurred_at : null,
      duration_ms: duration,
      ordering_mode: mode,
      summary: {
        event_count: ev.length,
        duration_ms: Date.parse(last.occurred_at) - Date.parse(first.occurred_at),
        llm_calls: ev.filter((e) => /^llm\.request\.(completed|failed)$/.test(e.event_type)).length,
        tool_calls: ev.filter((e) => /^tool\.call\.(completed|failed)$/.test(e.event_type)).length,
        input_tokens: sum("llm.request.completed", "llm.input_tokens"),
        output_tokens: sum("llm.request.completed", "llm.output_tokens"),
        estimated_cost_usd: Number(
          ev.reduce((s, e) => s + Number(e.attributes["cost.estimated_usd"] ?? 0), 0).toFixed(6),
        ),
        retry_count: ev.filter((e) => e.event_type === "retry.attempted").length,
        error_count: ev.filter(isErr).length,
        files_modified: files.size,
        models,
        first_event_at: first.occurred_at,
        last_event_at: last.occurred_at,
        ...agentSummary(ev),
      },
      first_error: firstErrorOut,
      summary_version: FIXTURE_SUMMARY_VERSION,
      summary_state: "current",
      metadata: {},
      created_at: first.occurred_at,
      updated_at: last.occurred_at,
    };
    const bySpan = new Map<string, EventOut[]>();
    for (const e of ev) if (e.span_id) bySpan.set(e.span_id, [...(bySpan.get(e.span_id) ?? []), e]);
    const spans: SpanOut[] = [...bySpan.entries()].map(([id, es]) => ({
      id,
      run_id: this.runId,
      trace_id: this.traceId,
      parent_span_id: es[0]!.parent_span_id ?? null,
      name: String(
        es[0]!.attributes["tool.name"] ?? es[0]!.attributes["llm.model"] ?? es[0]!.event_type,
      ),
      name_withheld: false,
      kind: es[0]!.event_type.split(".")[0] ?? null,
      agent_id: es[0]!.agent_id,
      status: es.find((e) => e.status)?.status ?? null,
      started_at: es[0]!.occurred_at,
      ended_at: es[es.length - 1]!.occurred_at,
      duration_ms: es.find((e) => e.duration_ms != null)?.duration_ms ?? null,
      event_count: es.length,
    }));
    return { run, events: ev, spans };
  }
}

/** `summary.agents` and `agent_count` for a run with more than one agent, shaped like the API worker's. */
function agentSummary(ev: EventOut[]): Record<string, unknown> {
  const ids = [...new Set(ev.map((e) => e.agent_id))];
  if (ids.length < 2) return {};
  const agentOfSpan = new Map<string, string>();
  for (const e of ev)
    if (e.span_id && !agentOfSpan.has(e.span_id)) agentOfSpan.set(e.span_id, e.agent_id);
  const agents = ids.map((id) => {
    const mine = ev.filter((e) => e.agent_id === id);
    const opener = mine.find((e) => e.event_type === "agent.started");
    const closer = [...mine].reverse().find((e) => e.event_type === "agent.completed");
    const parentSpan = opener?.parent_span_id;
    const parent = parentSpan ? (agentOfSpan.get(parentSpan) ?? null) : null;
    return {
      agent_id: id,
      parent_agent_id: parent === id ? null : parent,
      state: closer ? (closer.status === "error" ? "failed" : "completed") : "started",
      event_count: mine.length,
      llm_calls: mine.filter((e) => /^llm\.request\.(completed|failed)$/.test(e.event_type)).length,
      tool_calls: mine.filter((e) => /^tool\.call\.(completed|failed)$/.test(e.event_type)).length,
      error_count: mine.filter(
        (e) => e.status === "error" || e.status === "timeout" || e.event_type.endsWith(".failed"),
      ).length,
      estimated_cost_usd: Number(
        mine
          .reduce((sum, e) => sum + Number(e.attributes["cost.estimated_usd"] ?? 0), 0)
          .toFixed(6),
      ),
      first_event_at: mine.reduce(
        (m, e) => (e.occurred_at < m ? e.occurred_at : m),
        mine[0]!.occurred_at,
      ),
      last_event_at: mine.reduce(
        (m, e) => (e.occurred_at > m ? e.occurred_at : m),
        mine[0]!.occurred_at,
      ),
    };
  });
  return { agents, agent_count: agents.length };
}
