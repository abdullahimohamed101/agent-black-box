// The hero trace's data model. The fixture holds canonical events (the shape packages/event-schema validates, see
// tests/validate_fixture.py); this module turns the nine that are shown into plain rows and inspector text on the server, so
// the island receives serialisable data and the static export contains every row.

export interface FixtureEvent {
  event_id: string;
  event_type: string;
  occurred_at: string;
  sequence: number;
  agent_id: string;
  status?: "success" | "error" | "timeout" | "cancelled" | "blocked";
  duration_ms?: number;
  attributes: Record<string, string | number | boolean>;
  payload?: Record<string, unknown>;
}

export interface Fixture {
  title: string;
  started_at: string;
  shown: number[];
  figures: { duration: string; cost: string; tokens: string; retries: number; errors: number };
  events: FixtureEvent[];
}

export type RowStatus = "error" | "retry" | null;

export interface Inspector {
  type: string;
  meta: string;
  heading: string;
  text: string;
  facts: [string, string][];
  payload: string | null;
}

export interface TraceRow {
  seq: number;
  t: string;
  type: string;
  detail: string;
  dur: string;
  status: RowStatus;
  inspector: Inspector;
}

export interface TraceModel {
  title: string;
  date: string;
  headline: string;
  figures: Fixture["figures"];
  rows: TraceRow[];
  firstErrorIndex: number;
  firstErrorStep: number;
  total: number;
}

const int = new Intl.NumberFormat("en-US");
export const seconds = (ms: number) => `${(ms / 1000).toFixed(1)} s`;
export const offset = (ms: number) => (ms < 1000 ? `+${Math.round(ms)}ms` : `+${(ms / 1000).toFixed(1)}s`);
const str = (v: unknown) => String(v ?? "");
const money = (v: unknown) => `$${Number(v).toFixed(4)}`;

function detailOf(e: FixtureEvent): string {
  const a = e.attributes;
  switch (e.event_type) {
    case "run.started":
      return str(a["run.name"]);
    case "file.read":
      return str(a["file.path"]);
    case "llm.request.completed":
      return `${a["llm.model"]} · ${int.format(Number(a["llm.input_tokens"]))} in / ${int.format(Number(a["llm.output_tokens"]))} out · ${money(a["cost.estimated_usd"])}`;
    case "tool.call.started":
      return str(a["tool.name"]);
    case "tool.call.failed":
      return `${a["tool.name"]} · ${a["tool.error_type"]}`;
    case "retry.attempted":
      return `Retry #${a["retry.attempt"]} after ${a["retry.reason"]}`;
    default:
      return "";
  }
}

function inspect(e: FixtureEvent, t: number, first: FixtureEvent): Inspector {
  const a = e.attributes;
  const base = { type: e.event_type, meta: `${e.occurred_at.slice(0, 10)} ${e.occurred_at.slice(11, 19)}Z · #${e.sequence} · ${e.agent_id}`, payload: null as string | null };
  const dur = e.duration_ms != null ? seconds(e.duration_ms) : null;
  switch (e.event_type) {
    case "tool.call.failed":
      return {
        ...base,
        heading: "What went wrong",
        text: `${a["tool.name"]} failed: ${a["tool.error_type"]}`,
        facts: [["Tool", str(a["tool.name"])], ["Duration", dur ?? ""], ["Status", "error"]],
        payload: e.payload ? JSON.stringify(e.payload, null, 2) : null,
      };
    case "tool.call.started":
      return { ...base, heading: "Tool call", text: `Called ${a["tool.name"]}.`, facts: [["Tool", str(a["tool.name"])]] };
    case "llm.request.completed":
      return {
        ...base,
        heading: "Model call",
        text: detailOf(e) + (dur ? ` · ${dur}` : ""),
        facts: [["Model", str(a["llm.model"])], ["Tokens in", int.format(Number(a["llm.input_tokens"]))], ["Tokens out", int.format(Number(a["llm.output_tokens"]))], ["Cost", money(a["cost.estimated_usd"])], ["Duration", dur ?? ""]],
      };
    case "retry.attempted": {
      const gap = ((t - Date.parse(first.occurred_at)) / 1000).toFixed(1);
      return { ...base, heading: "Retry", text: `${detailOf(e)}, after a ${seconds(Number(a["retry.delay_ms"]))} wait.`, facts: [["Attempt", `#${a["retry.attempt"]}`], ["Delay", seconds(Number(a["retry.delay_ms"]))], ["Since first error", `${gap} s`]] };
    }
    case "file.read":
      return { ...base, heading: "File", text: `Read ${a["file.path"]}.`, facts: [["Path", str(a["file.path"])]] };
    case "run.started":
      return { ...base, heading: "What happened", text: `Run ${a["run.name"]} started by ${e.agent_id}.`, facts: [["Agent", e.agent_id]] };
    case "agent.started":
      return { ...base, heading: "What happened", text: `Agent ${e.agent_id} started.`, facts: [["Agent", e.agent_id]] };
    case "run.failed":
      return { ...base, heading: "What went wrong", text: `The run ended in failure. Its first error was step ${first.sequence}.`, facts: [["First error", `step ${first.sequence}`], ["Status", "error"]] };
    default:
      return { ...base, heading: "What happened", text: e.event_type, facts: [] };
  }
}

/** Nine rows out of the fixture's fifteen events; the first event with status "error" is the fault line. */
export function buildTrace(fx: Fixture): TraceModel {
  const t0 = Date.parse(fx.started_at);
  const first = fx.events.find((e) => e.status === "error");
  if (!first) throw new Error("fixture has no error event");
  const rows: TraceRow[] = fx.events
    .filter((e) => fx.shown.includes(e.sequence))
    .map((e) => {
      const ms = Date.parse(e.occurred_at) - t0;
      const status: RowStatus = e.status === "error" ? "error" : e.event_type === "retry.attempted" ? "retry" : null;
      return { seq: e.sequence, t: offset(ms), type: e.event_type, detail: detailOf(e), dur: e.duration_ms != null ? seconds(e.duration_ms) : "—", status, inspector: inspect(e, Date.parse(e.occurred_at), first) };
    });
  const firstErrorIndex = rows.findIndex((r) => r.seq === first.sequence);
  const after = ((Date.parse(first.occurred_at) - t0) / 1000).toFixed(1);
  return {
    title: fx.title,
    date: fx.started_at.slice(0, 10),
    headline: `Failed at step ${first.sequence} after ${after} s: ${first.attributes["tool.name"]}, ${first.attributes["tool.error_type"]}.`,
    figures: fx.figures,
    rows,
    firstErrorIndex,
    firstErrorStep: first.sequence,
    total: fx.events.length,
  };
}
