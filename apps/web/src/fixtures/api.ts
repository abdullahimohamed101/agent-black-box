import type { EventOut } from "@/lib/api/types";
import { fixtureAnalytics } from "./analytics";
import { fixtureSettings } from "./auth";
import {
  fixtureComparison,
  fixtureRunEvaluations,
  fixtureWorkspaceReply,
  type Viewer,
} from "./evaluations";
import { fixtureRuns, fixtureServedRuns } from "./index";
import { TRACE_STATE_AGENT } from "./traceStateRuns";
import { fixtureAlertReply } from "./alerts";
import { fixturePolicyReply } from "./policies";
import { fixtureQuotas } from "./quotas";
import { fixtureRetention } from "./retention";
import { fixtureSecurityDetectors, fixtureSecurityList, fixtureSecuritySettings } from "./security";
import { withholdEvent, withholdSpan } from "./withhold";
import { FixtureForbiddenError, FixtureQueryError, fixtureSearch } from "./search";

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
export function fixtureReply(
  segments: string[],
  q: URLSearchParams,
  viewer: Viewer = { content: true },
): Reply {
  const [v1, runs, runId, sub, eventId] = segments;
  const alerts = fixtureAlertReply(segments, q, { manage: viewer.content });
  if (alerts) return alerts;
  const policy = fixturePolicyReply(segments, q, viewer);
  if (policy) return policy;
  if (v1 === "v1" && segments.length === 2) {
    const body = fixtureSettings(runs ?? "");
    if (body) return { status: 200, body };
  }
  if (v1 === "v1" && runs === "analytics" && runId && !sub) {
    const body = fixtureAnalytics(runId, q);
    return body ? { status: 200, body } : notFound("NOT_FOUND", "Not found.");
  }
  if (v1 === "v1" && runs === "security" && runId === "findings" && !sub)
    return fixtureSecurityList(q);
  if (v1 === "v1" && runs === "security" && runId === "detectors" && !sub)
    return fixtureSecurityDetectors(q);
  if (v1 === "v1" && runs === "projects" && runId && sub === "security-settings")
    return fixtureSecuritySettings(runId, undefined, viewer.content);
  if (v1 === "v1" && runs === "projects" && runId && sub === "quotas")
    return fixtureQuotas(runId, undefined, viewer.content);
  if (v1 === "v1" && runs === "workspaces" && runId && sub === "retention")
    return fixtureRetention(runId, eventId, q, undefined, viewer.content);
  // The two runs whose trace retention removed (ADR-160) are reachable by id and by `agent_id=report-agent`, and stay out
  // of the default list, so the many tests and screens that count the fixture runs keep their numbers.
  const everything = fixtureServedRuns();
  const wantsRetained = !runId && q.get("agent_id") === TRACE_STATE_AGENT;
  const all = runId || wantsRetained ? everything : fixtureRuns();
  if (v1 === "v1" && (runs === "evaluators" || runs === "experiments")) {
    return fixtureWorkspaceReply(segments, all, viewer) ?? notFound("NOT_FOUND", "Not found.");
  }
  if (v1 !== "v1" || runs !== "runs") return notFound("NOT_FOUND", "Not found.");
  if (!runId) {
    let searched: ReturnType<typeof fixtureSearch> | null = null;
    const qText = q.get("q");
    if (qText !== null && qText.trim() !== "") {
      try {
        searched = fixtureSearch(all, qText, viewer);
      } catch (e) {
        if (e instanceof FixtureForbiddenError)
          return {
            status: 403,
            body: {
              error: {
                code: "PERMISSION_DENIED",
                message: "You do not have permission to search that field.",
                category: "AUTHORIZATION",
                retryable: false,
                request_id: "req_fixture",
                details: { field: e.field },
              },
            },
          };
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
    const p = page(viewer.content ? f.spans : f.spans.map(withholdSpan), q, 2000, 500);
    return p ? { status: 200, body: p } : invalid("Malformed cursor.");
  }
  if (sub === "findings" && !eventId) {
    return f.findings ? { status: 200, body: f.findings } : notFound("NOT_FOUND", "Not found.");
  }
  if (sub === "evaluations" && !eventId)
    return { status: 200, body: fixtureRunEvaluations(f, viewer) };
  if (sub === "compare" && !eventId) {
    const withId = q.get("with_run_id");
    const other = withId ? all.find((x) => x.run.id === withId) : undefined;
    if (!other) return notFound("RUN_NOT_FOUND", "Run not found.");
    // Like the API (ADR-160): comparing a run whose trace is gone is a conflict, not a comparison of nothing.
    if (f.run.trace_state !== "available" || other.run.trace_state !== "available")
      return {
        status: 409,
        body: {
          error: {
            code: "TRACE_EXPIRED",
            message: "The run's trace is no longer stored.",
            category: "CONFLICT",
            retryable: false,
            request_id: "req_fixture",
            details: {},
          },
        },
      };
    return { status: 200, body: fixtureComparison(f, other, viewer) };
  }
  if (sub !== "events") return notFound("NOT_FOUND", "Not found.");
  if (eventId) {
    const e = f.events.find((x) => x.event_id === eventId);
    return e
      ? { status: 200, body: viewer.content ? e : withholdEvent(e, true) }
      : notFound("EVENT_NOT_FOUND", "Event not found.");
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
      items: p.items.map((e) =>
        viewer.content ? { ...e, payload: null } : withholdEvent(e, false),
      ),
      ordering_mode: f.run.ordering_mode,
    },
  };
}
