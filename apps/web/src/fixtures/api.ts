import type { EventOut } from "@/lib/api/types";
import { fixtureRuns } from "./index";

export type Reply = { status: number; body: unknown };

const notFound = (code: string, message: string): Reply => ({
  status: 404,
  body: {
    error: {
      code,
      message,
      category: "NOT_FOUND",
      retryable: false,
      request_id: "req_fixture",
      details: {},
    },
  },
});
const invalid = (message: string): Reply => ({
  status: 400,
  body: {
    error: {
      code: "CURSOR_INVALID",
      message,
      category: "VALIDATION",
      retryable: false,
      request_id: "req_fixture",
      details: {},
    },
  },
});

const enc = (offset: number) => Buffer.from(`o:${offset}`).toString("base64url");
function dec(cursor: string | null): number | null {
  if (!cursor) return 0;
  const m = /^o:(\d+)$/.exec(Buffer.from(cursor, "base64url").toString());
  return m ? Number(m[1]) : null;
}

function page<T>(all: T[], q: URLSearchParams, max: number, def: number) {
  const offset = dec(q.get("cursor"));
  if (offset === null) return null;
  const limit = Math.min(Math.max(Number(q.get("limit") ?? def) || def, 1), max);
  const items = all.slice(offset, offset + limit);
  return { items, next_cursor: offset + limit < all.length ? enc(offset + limit) : null };
}

/** An in-memory implementation of the read API (§71, api-v1.md) over the deterministic fixtures. */
export function fixtureReply(segments: string[], q: URLSearchParams): Reply {
  const [v1, runs, runId, sub, eventId] = segments;
  if (v1 !== "v1" || runs !== "runs") return notFound("NOT_FOUND", "Not found.");
  const all = fixtureRuns();
  if (!runId) {
    let list = all.map((f) => f.run);
    const statuses = q.getAll("status");
    if (statuses.length) list = list.filter((r) => statuses.includes(r.status));
    const agent = q.get("agent_id");
    if (agent) list = list.filter((r) => r.agent_id === agent);
    const project = q.get("project_id");
    if (project) list = list.filter((r) => r.project_id === project);
    const after = q.get("started_after");
    if (after) list = list.filter((r) => Date.parse(r.started_at) >= Date.parse(after));
    const before = q.get("started_before");
    if (before) list = list.filter((r) => Date.parse(r.started_at) < Date.parse(before));
    if (q.get("sort") === "started_at") list = [...list].reverse();
    const p = page(list, q, 200, 50);
    return p ? { status: 200, body: p } : invalid("Malformed cursor.");
  }
  const f = all.find((x) => x.run.id === runId);
  if (!f) return notFound("RUN_NOT_FOUND", "Run not found.");
  if (!sub) return { status: 200, body: f.run };
  if (sub === "spans") {
    const p = page(f.spans, q, 2000, 500);
    return p ? { status: 200, body: p } : invalid("Malformed cursor.");
  }
  if (sub !== "events") return notFound("NOT_FOUND", "Not found.");
  if (eventId) {
    const e = f.events.find((x) => x.event_id === eventId);
    return e ? { status: 200, body: e } : notFound("EVENT_NOT_FOUND", "Event not found.");
  }
  let evs: EventOut[] = f.events;
  const types = q.getAll("event_type");
  if (types.length) evs = evs.filter((e) => types.includes(e.event_type));
  const st = q.getAll("status");
  if (st.length) evs = evs.filter((e) => e.status && st.includes(e.status));
  const span = q.get("span_id");
  if (span) evs = evs.filter((e) => e.span_id === span);
  const p = page(evs, q, 500, 100);
  if (!p) return invalid("Malformed cursor.");
  // Lists never carry payloads (api-v1.md); detail does.
  return {
    status: 200,
    body: {
      ...p,
      items: p.items.map((e) => ({ ...e, payload: null })),
      ordering_mode: f.run.ordering_mode,
    },
  };
}
