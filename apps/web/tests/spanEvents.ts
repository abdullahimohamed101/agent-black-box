/** Event factories for span tests: plain events with a span id, a parent and a second-based clock. */
import type { EventOut } from "@/lib/api/types";

export const BASE = Date.parse("2026-10-07T12:00:00Z");
export const at = (s: number) => new Date(BASE + s * 1000).toISOString();
export const spn = (n: number) => `spn_${String(n).padStart(26, "0")}`;
let counter = 0;

export type Opts = {
  parent?: number | null;
  agent?: string;
  status?: string | null;
  attrs?: Record<string, unknown>;
  withheld?: string[];
};

export function ev(type: string, span: number, t: number, o: Opts = {}): EventOut {
  counter += 1;
  return {
    schema_version: "1.0",
    event_id: `evt_${String(counter).padStart(26, "0")}`,
    workspace_id: "wsp_1",
    project_id: "prj_1",
    run_id: "run_1",
    trace_id: "trc_1",
    span_id: spn(span),
    parent_span_id: o.parent ? spn(o.parent) : null,
    agent_id: o.agent ?? "root",
    event_type: type,
    occurred_at: at(t),
    received_at: at(t),
    sequence: null,
    status: o.status ?? null,
    duration_ms: null,
    attributes: o.attrs ?? {},
    payload: null,
    payload_ref: null,
    has_payload: false,
    payload_withheld: false,
    cost_unpriced: false,
    tags: [],
    withheld_attributes: o.withheld ?? [],
  } as EventOut;
}

/** A tool span from `start` to `end` seconds; `end = null` leaves it open. */
export function tool(
  id: number,
  start: number,
  end: number | null,
  o: Opts & { name?: string } = {},
): EventOut[] {
  const attrs = { "tool.name": o.name ?? `t${id}`, ...o.attrs };
  const out = [ev("tool.call.started", id, start, { ...o, attrs })];
  if (end !== null)
    out.push(ev("tool.call.completed", id, end, { ...o, attrs, status: "success" }));
  return out;
}
