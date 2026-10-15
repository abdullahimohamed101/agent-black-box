import type { components } from "@/lib/api/schema";
import { FIXTURE_USER } from "./auth";
import { FIXTURE_PROJECT } from "./builder";

/**
 * The alert routes of the fixture API (Phase 17), with in-memory state so the Alerts screens can be exercised without an
 * API. Honesty notes: the target check is a small stand-in for the API's (it knows `not_https`, `port_not_allowed` and `host_internal`, not the
 * whole list); "Send test" answers by destination (the Slack one is failing and answers `http_4xx`); email is reported
 * as not configured. The signing secret returned on create/rotate is a made-up fixture value. Non-managers
 * (`manage: false`, the viewer workspace) get the API's role behaviour: no destinations, masked history rows.
 * State lives in the server process and resets when it restarts.
 */

type Reply = { status: number; body: unknown };
type Dest = components["schemas"]["DestinationOut"];
type Rule = components["schemas"]["RuleOut"];
type Note = components["schemas"]["NotificationOut"];
export type AlertViewer = { manage: boolean };

const err = (status: number, code: string, message: string, details: object = {}): Reply => ({
  status,
  body: {
    error: {
      code,
      message,
      category: "FIXTURE",
      retryable: false,
      request_id: "req_fixture",
      details,
    },
  },
});
const denied = () => err(403, "PERMISSION_DENIED", "Your role does not allow this.");
const AT = "2026-10-07T09:00:00Z";
const pad = (p: string, n: number) => `${p}_${String(n).padStart(26, "0")}`;

let dests: Dest[] | undefined;
let rules: Rule[] | undefined;
let notes: Note[] | undefined;
let seq = 100;

const dest = (n: number, over: Partial<Dest>): Dest => ({
  id: pad("dst", n),
  name: "",
  type: "webhook",
  masked_target: "",
  state: "active",
  consecutive_failures: 0,
  paused_reason: null,
  rule_count: 0,
  created_by: FIXTURE_USER.id,
  created_at: AT,
  updated_by: FIXTURE_USER.id,
  updated_at: AT,
  ...over,
});

function seed() {
  dests = [
    dest(1, {
      name: "Ops webhook",
      masked_target: "https://hooks.example.com/…a91f",
      rule_count: 2,
    }),
    dest(2, {
      name: "Team Slack",
      type: "slack",
      masked_target: "https://hooks.slack.com/services/…Zk3q",
      state: "failing",
      consecutive_failures: 4,
      rule_count: 1,
    }),
  ];
  const ref = (d: Dest) => ({ id: d.id, name: d.name, state: d.state, type: d.type });
  const base = (n: number, over: Partial<Rule>): Rule => ({
    id: pad("rul", n),
    name: "",
    trigger: "run_failed",
    project_id: null,
    filters: {},
    cooldown_seconds: 0,
    enabled: true,
    last_fired_at: null,
    destination_count: 0,
    destinations: [],
    created_by: FIXTURE_USER.id,
    created_at: AT,
    updated_by: FIXTURE_USER.id,
    updated_at: AT,
    ...over,
  });
  const [a, b] = dests;
  rules = [
    base(1, {
      name: "Failed runs",
      trigger: "run_failed",
      project_id: FIXTURE_PROJECT,
      filters: { run_statuses: ["FAILED", "TIMED_OUT"] },
      cooldown_seconds: 600,
      last_fired_at: "2026-10-12T14:30:00Z",
      destinations: [ref(a!), ref(b!)],
      destination_count: 2,
    }),
    base(2, {
      name: "High severity findings",
      trigger: "security_finding",
      filters: { min_severity: "high", agent: "release-agent" },
      last_fired_at: "2026-10-11T08:05:00Z",
      destinations: [ref(a!)],
      destination_count: 1,
    }),
  ];
  const n = (i: number, over: Partial<Note>): Note => ({
    id: pad("alt", i),
    rule: { id: pad("rul", 1), name: "Failed runs" },
    destination: { id: a!.id, name: a!.name, type: "webhook" },
    trigger: "run_failed",
    severity: "high",
    status: "delivered",
    skip_reason: null,
    attempts: 1,
    error_code: null,
    project_id: FIXTURE_PROJECT,
    run_id: "run_00000000000000000000000001",
    finding_id: null,
    approval_id: null,
    agent_slug: "release-agent",
    created_at: new Date(Date.UTC(2026, 9, 12, 14, 30 - i * 7)).toISOString(),
    delivered_at: new Date(Date.UTC(2026, 9, 12, 14, 30 - i * 7, 2)).toISOString(),
    next_attempt_at: null,
    ...over,
  });
  notes = [
    n(0, {}),
    n(1, {
      status: "pending",
      attempts: 2,
      delivered_at: null,
      error_code: "http_5xx",
      next_attempt_at: "2026-10-12T15:00:00Z",
    }),
    n(2, {
      destination: { id: b!.id, name: b!.name, type: "slack" },
      status: "dead",
      attempts: 6,
      delivered_at: null,
      error_code: "http_4xx",
    }),
    n(3, { status: "skipped", skip_reason: "cooldown", attempts: 0, delivered_at: null }),
    n(4, {
      rule: { id: pad("rul", 2), name: "High severity findings" },
      trigger: "security_finding",
      severity: "critical",
      finding_id: "fnd_00000000000000000000000001",
    }),
    n(5, {
      rule: null,
      status: "skipped",
      skip_reason: "rule_deleted",
      attempts: 0,
      delivered_at: null,
    }),
    n(6, {
      trigger: "approval_requested",
      severity: "info",
      approval_id: "apr_00000000000000000000000001",
      run_id: null,
    }),
    n(7, { status: "pending", attempts: 0, delivered_at: null }),
    n(8, { status: "failed", attempts: 1, delivered_at: null, error_code: "timeout" }),
    n(9, {}),
    n(10, {
      destination: null,
      status: "skipped",
      skip_reason: "destination_deleted",
      attempts: 0,
      delivered_at: null,
    }),
  ];
}
const state = () => {
  if (!dests) seed();
  return { dests: dests!, rules: rules!, notes: notes! };
};
export function resetAlertFixtures(): void {
  dests = rules = notes = undefined;
  seq = 100;
}

const enc = (o: number) => Buffer.from(`o:${o}`).toString("base64url");
const rulesFor = (v: AlertViewer): Rule[] =>
  state().rules.map((r) => (v.manage ? r : { ...r, destinations: [] }));

export const isAlertFixturePath = (segments: string[]): boolean =>
  segments[0] === "v1" && segments[1] === "alerts";

export function fixtureAlertReply(
  segments: string[],
  q: URLSearchParams,
  v: AlertViewer,
): Reply | null {
  if (!isAlertFixturePath(segments)) return null;
  const [, , area, id, sub] = segments;
  const s = state();
  if (area === "destinations" && !id)
    return v.manage ? { status: 200, body: { items: s.dests, email_configured: false } } : denied();
  if (area === "rules" && !id) return { status: 200, body: { items: rulesFor(v) } };
  if (area === "rules" && id && sub === "preview") {
    if (!s.rules.some((r) => r.id === id))
      return err(404, "ALERT_RULE_NOT_FOUND", "Rule not found.");
    const days = Math.min(30, Math.max(1, Number(q.get("days") ?? 7) || 7));
    const daily = Array.from({ length: days }, (_, i) => ({
      day: new Date(Date.UTC(2026, 9, 14 - (days - 1 - i))).toISOString().slice(0, 10),
      count: i % 3 === 0 ? 1 : 0,
    }));
    return { status: 200, body: { days, count: daily.reduce((a, d) => a + d.count, 0), daily } };
  }
  if (area === "notifications" && !id) {
    if (q.get("destination_id") && !v.manage) return denied();
    let list = s.notes.filter(
      (n) =>
        (!q.get("status") || n.status === q.get("status")) &&
        (!q.get("trigger") || n.trigger === q.get("trigger")) &&
        (!q.get("rule_id") || n.rule?.id === q.get("rule_id")) &&
        (!q.get("destination_id") || n.destination?.id === q.get("destination_id")),
    );
    if (!v.manage)
      list = list.map((n) => ({
        ...n,
        approval_id: null,
        destination: n.destination ? { id: null, name: null, type: n.destination.type } : null,
      })) as Note[];
    const cursor = q.get("cursor");
    const m = cursor ? /^o:(\d+)$/.exec(Buffer.from(cursor, "base64url").toString()) : null;
    if (cursor && !m) return err(400, "CURSOR_INVALID", "Malformed cursor.");
    const offset = m ? Number(m[1]) : 0;
    const limit = Math.min(Math.max(Number(q.get("limit") ?? 50) || 50, 1), 200);
    return {
      status: 200,
      body: {
        items: list.slice(offset, offset + limit),
        next_cursor: offset + limit < list.length ? enc(offset + limit) : null,
      },
    };
  }
  return err(404, "NOT_FOUND", "Not found.");
}

const obj = (b: unknown): Record<string, unknown> =>
  b && typeof b === "object" ? (b as Record<string, unknown>) : {};

function targetProblem(type: string, target: Record<string, unknown>): string | null {
  if (type === "email") {
    const r = target.recipients;
    if (!Array.isArray(r) || r.length === 0) return "recipients_required";
    if (r.length > 10) return "too_many_recipients";
    return r.every((x) => typeof x === "string" && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(x))
      ? null
      : "recipient_invalid";
  }
  const url = target.url;
  if (typeof url !== "string" || !url) return "url_required";
  if (!/^https:\/\//i.test(url)) return /^http:\/\//i.test(url) ? "not_https" : "url_malformed";
  const host = /^https:\/\/([^/:?#]+)/i.exec(url)?.[1] ?? "";
  if (!host) return "host_invalid";
  const port = /^https:\/\/[^/:?#]+:(\d+)/i.exec(url)?.[1];
  if (port !== undefined && port !== "443") return "port_not_allowed";
  if (/(^localhost$|\.internal$|\.local$|^127\.|^10\.|^192\.168\.)/i.test(host))
    return "host_internal";
  return null;
}

/** POST/PATCH/DELETE under /v1/alerts. Null when the path is not one of the alert routes. */
export function fixtureAlertWrite(
  method: string,
  segments: string[],
  body: unknown,
  v: AlertViewer,
): Reply | null {
  if (!isAlertFixturePath(segments)) return null;
  if (!v.manage) return denied();
  const [, , area, id, sub] = segments;
  const s = state();
  const b = obj(body);
  const taken = (list: { name: string; id: string }[], name: string, self?: string) =>
    list.some((x) => x.id !== self && x.name.toLowerCase() === name.toLowerCase());
  const now = new Date().toISOString();

  if (area === "destinations") {
    if (method === "POST" && !id) {
      const name = String(b.name ?? "").trim();
      const type = String(b.type ?? "") as Dest["type"];
      if (!name || !["webhook", "slack", "email"].includes(type))
        return err(422, "VALIDATION_ERROR", "Invalid request.", {
          errors: [{ loc: ["body", "name"], msg: "Enter a name." }],
        });
      if (type === "email") return err(422, "EMAIL_NOT_CONFIGURED", "Email is not configured.");
      if (taken(s.dests, name)) return err(409, "ALERT_NAME_TAKEN", "Name taken.");
      const reason = targetProblem(type, obj(b.target));
      if (reason) return err(422, "DESTINATION_TARGET_INVALID", "Invalid target.", { reason });
      const d = dest(++seq, {
        name,
        type,
        masked_target: "https://…" + String(obj(b.target).url).slice(-4),
        created_at: now,
        updated_at: now,
      });
      s.dests.push(d);
      return {
        status: 201,
        body: {
          ...d,
          signing_secret:
            type === "webhook" ? `whsec_fixture${String(seq).padStart(4, "0")}` : null,
        },
      };
    }
    const d = s.dests.find((x) => x.id === id);
    if (!d) return err(404, "ALERT_DESTINATION_NOT_FOUND", "Destination not found.");
    if (method === "POST" && sub === "test")
      return {
        status: 200,
        body:
          d.type === "slack"
            ? { ok: false, error_code: "http_4xx" }
            : { ok: true, error_code: null },
      };
    if (method === "DELETE" && !sub) {
      s.dests = s.dests.filter((x) => x !== d);
      dests = s.dests;
      return { status: 204, body: null };
    }
    if (method === "PATCH" && !sub) {
      if (b.state === "paused") Object.assign(d, { state: "paused", paused_reason: "manual" });
      if (b.state === "active")
        Object.assign(d, { state: "active", paused_reason: null, consecutive_failures: 0 });
      if (typeof b.name === "string" && b.name.trim()) d.name = b.name.trim();
      d.updated_at = now;
      if (b.rotate_signing_secret === true) {
        if (d.type !== "webhook")
          return err(422, "DESTINATION_INVALID", "No signing secret.", {
            reason: "no_signing_secret",
          });
        return {
          status: 200,
          body: { ...d, signing_secret: `whsec_fixture_rotated${String(++seq).padStart(4, "0")}` },
        };
      }
      return { status: 200, body: { ...d, signing_secret: null } };
    }
  }

  if (area === "rules") {
    const refs = (ids: unknown) =>
      (Array.isArray(ids) ? ids : [])
        .map((x) => s.dests.find((d) => d.id === x))
        .filter((d): d is Dest => !!d)
        .map((d) => ({ id: d.id, name: d.name, state: d.state, type: d.type }));
    if (method === "POST" && !id) {
      const name = String(b.name ?? "").trim();
      const trigger = String(b.trigger ?? "") as Rule["trigger"];
      if (!name)
        return err(422, "VALIDATION_ERROR", "Invalid request.", {
          errors: [{ loc: ["body", "name"], msg: "Enter a name." }],
        });
      if (taken(s.rules, name)) return err(409, "ALERT_NAME_TAKEN", "Name taken.");
      const filters = obj(b.filters);
      const need: Record<string, string> = {
        security_finding: "min_severity",
        approval_expiring: "expiring_lead_seconds",
        spend_over: "threshold_usd",
      };
      if (need[trigger] && filters[need[trigger]!] == null)
        return err(422, "RULE_INVALID", "Invalid rule.", {
          field: need[trigger],
          reason: "required",
        });
      if (filters.agent === "nobody")
        return err(422, "RULE_INVALID", "Invalid rule.", {
          field: "agent",
          reason: "unknown_agent",
        });
      const r: Rule = {
        id: pad("rul", ++seq),
        name,
        trigger,
        project_id: (b.project_id as string | null) ?? null,
        filters: filters as Rule["filters"],
        cooldown_seconds: Number(b.cooldown_seconds ?? 0),
        enabled: b.enabled !== false,
        last_fired_at: null,
        destinations: refs(b.destination_ids),
        destination_count: refs(b.destination_ids).length,
        created_by: FIXTURE_USER.id,
        created_at: now,
        updated_by: FIXTURE_USER.id,
        updated_at: now,
      };
      s.rules.push(r);
      return { status: 201, body: r };
    }
    const r = s.rules.find((x) => x.id === id);
    if (!r) return err(404, "ALERT_RULE_NOT_FOUND", "Rule not found.");
    if (method === "DELETE") {
      rules = s.rules.filter((x) => x !== r);
      return { status: 204, body: null };
    }
    if (method === "PATCH") {
      if (typeof b.name === "string" && taken(s.rules, b.name.trim(), r.id))
        return err(409, "ALERT_NAME_TAKEN", "Name taken.");
      if (typeof b.name === "string") r.name = b.name.trim();
      if ("project_id" in b) r.project_id = (b.project_id as string | null) ?? null;
      if (b.filters) r.filters = b.filters as Rule["filters"];
      if (b.destination_ids) {
        r.destinations = refs(b.destination_ids);
        r.destination_count = r.destinations.length;
      }
      if (typeof b.cooldown_seconds === "number") r.cooldown_seconds = b.cooldown_seconds;
      if (typeof b.enabled === "boolean") r.enabled = b.enabled;
      r.updated_at = now;
      return { status: 200, body: r };
    }
  }
  return err(405, "METHOD_NOT_ALLOWED", "Method not allowed.");
}
