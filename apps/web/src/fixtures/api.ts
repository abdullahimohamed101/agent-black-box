import type { EventOut } from "@/lib/api/types";
import { fixtureAnalytics } from "./analytics";
import { fixtureSettings } from "./auth";
import { fixtureRuns } from "./index";
import { FixtureQueryError, fixtureSearch } from "./search";

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
  if (v1 === "v1" && segments.length === 2) {
    const body = fixtureSettings(runs ?? "");
    if (body) return { status: 200, body };
  }
  if (v1 === "v1" && runs === "analytics" && runId && !sub) {
    const body = fixtureAnalytics(runId, q);
    return body ? { status: 200, body } : notFound("NOT_FOUND", "Not found.");
  }
  if (v1 !== "v1" || runs !== "runs") return notFound("NOT_FOUND", "Not found.");
  const all = fixtureRuns();
  if (!runId) {
    let searched: ReturnType<typeof fixtureSearch> | null = null;
    const qText = q.get("q");
    if (qText !== null && qText.trim() !== "") {
      try {
        searched = fixtureSearch(all, qText);
      } catch (e) {
        if (!(e instanceof FixtureQueryError)) throw e;
        return {
          status: 422,
          body: {
            error: {
              code: "QUERY_INVALID",
              message: `Invalid search: ${e.reason}.`,
              category: "VALIDATION",
              retryable: false,
              request_id: "req_fixture",
              details: { position: e.position, reason: e.reason },
            },
          },
        };
      }
    }
    let list = searched ? searched.runs : all.map((f) => f.run);
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
    if (!p) return invalid("Malformed cursor.");
    if (searched)
      return { status: 200, body: { ...p, query: searched.query, facets: searched.facets } };
    // `q=` alone asks for the catalogue (the search help); the list is the plain one.
    if (qText !== null) return { status: 200, body: { ...p, query: fixtureSearch(all, "").query } };
    return { status: 200, body: p };
  }
  const f = all.find((x) => x.run.id === runId);
  if (!f) return notFound("RUN_NOT_FOUND", "Run not found.");
  if (!sub) return { status: 200, body: f.run };
  if (sub === "spans") {
    const p = page(f.spans, q, 2000, 500);
    return p ? { status: 200, body: p } : invalid("Malformed cursor.");
  }
  if (sub === "findings" && !eventId) {
    return f.findings ? { status: 200, body: f.findings } : notFound("NOT_FOUND", "Not found.");
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
