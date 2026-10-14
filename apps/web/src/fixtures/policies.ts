import { createHash } from "node:crypto";
import type { components } from "@/lib/api/schema";
import { STARTER_POLICY } from "@/lib/policy";
import { FIXTURE_PROJECT } from "./builder";
import { FIXTURE_USER } from "./auth";
import { fixtureRuns } from "./index";
import {
  USED_APPROVAL_ID,
  WAITING_APPROVAL_ID,
  WAITING_RUN_N,
  DECISIONS_RUN_N,
} from "./policyRuns";
import { fid } from "./ids";

/**
 * The policy and approval routes of the fixture API (Phase 14), with in-memory state so the Approvals and Policies screens
 * can be exercised without an API: pending and decided approvals with the API's error codes, a policy per scope with
 * versions, a validator, a dry run and publish.
 *
 * Honesty notes. The validator below is a line-based approximation of the real parser (`parser.py`): it knows the codes
 * and sentences of the mistakes people actually make, not the whole language. The dry run is canned and deterministic: its
 * counts depend only on which rules the draft names. Three approvals trigger the API's refusals by their id so the
 * screens can show every error without a second person: `...04` is a request made with the viewer's own key
 * (SELF_APPROVAL_FORBIDDEN), `...05` needs a role the viewer does not hold (APPROVER_ROLE_REQUIRED), `...06` is decided
 * by someone else first (APPROVAL_NOT_REQUESTED). State lives in the server process and resets when it restarts.
 */

type Reply = { status: number; body: unknown };
type Detail = components["schemas"]["ApprovalDetail"];
type Summary = components["schemas"]["ApprovalSummary"];
type VersionDetail = components["schemas"]["PolicyVersionDetail"];
type Issue = components["schemas"]["abb_api__policies__schemas__IssueOut"];

const err = (
  status: number,
  code: string,
  message: string,
  category: string,
  details: Record<string, unknown> = {},
): Reply => ({
  status,
  body: {
    error: { code, message, category, retryable: false, request_id: "req_fixture", details },
  },
});
const forbidden = (): Reply =>
  err(403, "PERMISSION_DENIED", "Your role does not allow this.", "AUTHORIZATION");
const notFound = (code: string, message: string): Reply => err(404, code, message, "NOT_FOUND");

export type FixtureViewer = { content: boolean };

// ---- approvals --------------------------------------------------------------------------------------------------

const aid = (n: number) => `apr_${String(n).padStart(26, "0")}`;
const MIN = 60_000;
const HOUR = 60 * MIN;
const CAPS: { capability: string; resource: string; risk: string | null; args: object }[] = [
  { capability: "github.push", resource: "origin main", risk: "R3", args: { branch: "main", force: false } },
  { capability: "shell.execute", resource: "rm", risk: "R3", args: { category: "DESTRUCTIVE", command: "rm -r build/" } },
  { capability: "database.execute", resource: "orders", risk: "R3", args: { statement: "ALTER TABLE orders ADD COLUMN note text" } },
  { capability: "filesystem.write", resource: "deploy/prod.yaml", risk: null, args: { operation: "modify" } },
  { capability: "github.merge", resource: "release/2026.10", risk: "R3", args: { method: "squash" } },
]; // prettier-ignore

type Rec = Detail & { _expires: number };
let T0: number | undefined;
let store: Map<string, Rec> | undefined;

function seed(): Map<string, Rec> {
  T0 ??= Date.now();
  const now = T0;
  const m = new Map<string, Rec>();
  const base = (n: number, over: Partial<Detail> & { expiresMs: number }): Rec => {
    const c = CAPS[n % CAPS.length]!;
    const { expiresMs, ...rest } = over;
    const requestedAt = now - 8 * MIN - n * 3 * MIN;
    const rec: Detail = {
      id: aid(n),
      agent_id: "release-agent",
      allow_self_approval: false,
      approver_role: "ADMIN",
      arguments: c.args as Record<string, unknown>,
      capability: c.capability,
      closed_at: null,
      comment: null,
      consumed: false,
      consumed_at: null,
      content_withheld: false,
      decided_at: null,
      decided_by: null,
      expires_at: new Date(now + expiresMs).toISOString(),
      on_expiry: "deny",
      policy_id: `approve-${c.capability.replace(".", "-")}`,
      policy_version: 3,
      project_id: FIXTURE_PROJECT,
      redactions: 0,
      requested_at: new Date(requestedAt).toISOString(),
      resource: c.resource,
      risk_class: c.risk,
      run_id: fid("run", 5),
      seconds_left: null,
      state: "REQUESTED",
      ...rest,
    };
    return { ...rec, _expires: now + expiresMs };
  };
  const put = (r: Rec) => m.set(r.id, r);
  // Pending (long expiries so a whole test run never sees them expire; the countdown is exercised by unit tests)
  put(
    base(1, {
      expiresMs: 6 * HOUR,
      run_id: fid("run", WAITING_RUN_N),
      policy_id: "approve-push-main",
      capability: "github.push",
      resource: "origin main",
      risk_class: "R3",
      arguments: { branch: "main", force: false, remote: "origin" },
    }),
  );
  put(
    base(2, {
      expiresMs: 4 * HOUR,
      run_id: fid("run", 5),
      capability: "shell.execute",
      resource: "kubectl",
      risk_class: "R3",
      policy_id: "approve-risky-shell",
      arguments: { category: "PRIVILEGED", command: "kubectl apply -f deploy/staging.yaml" },
    }),
  );
  put(
    base(3, {
      expiresMs: 3 * HOUR,
      capability: "database.execute",
      resource: "orders",
      risk_class: "R3",
      policy_id: "approve-db-write",
      arguments: { statement: "ALTER TABLE orders ADD COLUMN note text" },
    }),
  );
  put(
    base(4, {
      expiresMs: 2 * HOUR,
      capability: "github.merge",
      resource: "release/2026.10",
      risk_class: "R3",
      policy_id: "approve-merge",
      arguments: { method: "squash" },
    }),
  );
  put(
    base(5, {
      expiresMs: 5 * HOUR,
      capability: "secrets.read",
      resource: "prod/db-password",
      risk_class: "R4",
      approver_role: "OWNER",
      policy_id: "owner-secrets",
      arguments: { version: "latest" },
    }),
  );
  put(
    base(6, {
      expiresMs: 90 * MIN,
      capability: "shell.execute",
      resource: "terraform",
      risk_class: "R3",
      policy_id: "approve-risky-shell",
      arguments: { category: "PRIVILEGED", command: "terraform apply" },
    }),
  );
  // Decided, in the order they happened
  const decided = (n: number, state: Detail["state"], over: Partial<Detail> = {}) =>
    put(
      base(n, {
        expiresMs: -30 * MIN,
        state,
        decided_at: new Date(now - 40 * MIN - n * MIN).toISOString(),
        decided_by: FIXTURE_USER.id,
        ...over,
      }),
    );
  decided(11, "APPROVED", {
    run_id: fid("run", DECISIONS_RUN_N),
    policy_id: "approve-push-main",
    capability: "github.push",
    resource: "origin main",
    risk_class: "R3",
    arguments: { branch: "main", force: false, remote: "origin" },
    consumed: true,
    consumed_at: new Date(now - 38 * MIN).toISOString(),
    comment: "Release notes reviewed.",
  });
  decided(12, "DENIED", {
    capability: "shell.execute",
    resource: "chmod",
    risk_class: "R3",
    policy_id: "approve-risky-shell",
    arguments: { category: "DESTRUCTIVE", command: "chmod -R 777 /srv" },
    comment: "Not on a shared host.",
  });
  decided(13, "EXPIRED", {
    decided_at: null,
    decided_by: null,
    closed_at: new Date(now - 50 * MIN).toISOString(),
    expires_at: new Date(now - 50 * MIN).toISOString(),
  });
  decided(14, "CANCELLED", {
    decided_at: null,
    decided_by: null,
    closed_at: new Date(now - 55 * MIN).toISOString(),
  });
  decided(15, "APPROVED", {
    capability: "filesystem.write",
    resource: "deploy/prod.yaml",
    risk_class: null,
    policy_id: "approve-prod-config",
    arguments: { operation: "modify" },
  });
  // Enough older decided requests that "Load more" has a second page.
  for (let i = 0; i < 30; i++) {
    const n = 100 + i;
    const kind = i % 3 === 0 ? "DENIED" : i % 3 === 1 ? "APPROVED" : "EXPIRED";
    const c = CAPS[i % CAPS.length]!;
    const state = kind as Detail["state"];
    m.set(aid(n), {
      ...base(n, {
        expiresMs: -(2 + i) * HOUR,
        state,
        capability: c.capability,
        resource: c.resource,
        risk_class: c.risk,
        arguments: c.args as Record<string, unknown>,
        decided_at: state === "EXPIRED" ? null : new Date(now - (2 + i) * HOUR).toISOString(),
        decided_by: state === "EXPIRED" ? null : FIXTURE_USER.id,
        closed_at: state === "EXPIRED" ? new Date(now - (2 + i) * HOUR).toISOString() : null,
      }),
      requested_at: new Date(now - (3 + i) * HOUR).toISOString(),
    });
  }
  return m;
}

function approvals(): Map<string, Rec> {
  store ??= seed();
  const now = Date.now();
  for (const r of store.values())
    if (r.state === "REQUESTED" && r._expires <= now) {
      r.state = "EXPIRED";
      r.closed_at = new Date(r._expires).toISOString();
    }
  return store;
}

const view = (r: Rec): Detail => {
  const { _expires, ...d } = r;
  return {
    ...d,
    seconds_left:
      r.state === "REQUESTED" ? Math.max(0, Math.floor((_expires - Date.now()) / 1000)) : null,
  };
};
const summary = (d: Detail): Summary => ({
  agent_id: d.agent_id,
  approver_role: d.approver_role,
  capability: d.capability,
  consumed: d.consumed,
  decided_at: d.decided_at,
  decided_by: d.decided_by,
  expires_at: d.expires_at,
  id: d.id,
  policy_id: d.policy_id,
  policy_version: d.policy_version,
  project_id: d.project_id,
  requested_at: d.requested_at,
  resource: d.resource,
  risk_class: d.risk_class,
  run_id: d.run_id,
  seconds_left: d.seconds_left,
  state: d.state,
});

const enc = (offset: number) => Buffer.from(`o:${offset}`).toString("base64url");
function dec(cursor: string | null): number | null {
  if (!cursor) return 0;
  const m = /^o:(\d+)$/.exec(Buffer.from(cursor, "base64url").toString());
  return m ? Number(m[1]) : null;
}

function listApprovals(q: URLSearchParams): Reply {
  const state = q.get("state") ?? "pending";
  const project = q.get("project_id");
  let all = [...approvals().values()].map(view);
  if (state === "pending") all = all.filter((a) => a.state === "REQUESTED");
  else if (state === "decided") all = all.filter((a) => a.state !== "REQUESTED");
  if (project) all = all.filter((a) => a.project_id === project);
  all.sort((a, b) => b.requested_at.localeCompare(a.requested_at) || b.id.localeCompare(a.id));
  const offset = dec(q.get("cursor"));
  if (offset === null) return err(400, "CURSOR_INVALID", "Malformed cursor.", "VALIDATION");
  const limit = Math.min(Math.max(Number(q.get("limit") ?? 25) || 25, 1), 100);
  return {
    status: 200,
    body: {
      items: all.slice(offset, offset + limit).map(summary),
      next_cursor: offset + limit < all.length ? enc(offset + limit) : null,
    },
  };
}

function decideApproval(id: string, body: unknown): Reply {
  const rec = approvals().get(id);
  if (!rec) return notFound("APPROVAL_NOT_FOUND", "Approval not found.");
  const b = (body ?? {}) as { decision?: unknown; comment?: unknown };
  if (b.decision !== "approve" && b.decision !== "deny")
    return err(422, "VALIDATION_ERROR", "The request is not valid.", "VALIDATION");
  if (typeof b.comment === "string" && b.comment.length > 1000)
    return err(422, "VALIDATION_ERROR", "The request is not valid.", "VALIDATION");
  const n = Number(id.slice(-6));
  if (rec.state === "REQUESTED") {
    if (n === 5)
      return err(
        403,
        "APPROVER_ROLE_REQUIRED",
        "Your role may not decide this approval.",
        "AUTHORIZATION",
      );
    if (n === 4)
      return err(
        403,
        "SELF_APPROVAL_FORBIDDEN",
        "You cannot decide a request made with your own API key.",
        "AUTHORIZATION",
      );
    if (n === 6) {
      rec.state = "DENIED";
      rec.decided_at = new Date().toISOString();
      rec.decided_by = "usr_00000000000000000000000002";
      return err(409, "APPROVAL_NOT_REQUESTED", "The approval is no longer waiting.", "CONFLICT", {
        state: "DENIED",
      });
    }
    if (b.decision === "approve" && rec.content_withheld)
      return err(
        409,
        "APPROVAL_CONTENT_UNAVAILABLE",
        "The content of this request could not be shown, so it cannot be approved.",
        "CONFLICT",
      );
    rec.state = b.decision === "approve" ? "APPROVED" : "DENIED";
    rec.decided_at = new Date().toISOString();
    rec.decided_by = FIXTURE_USER.id;
    rec.comment = typeof b.comment === "string" && b.comment ? b.comment : null;
    return { status: 200, body: view(rec) };
  }
  return err(409, "APPROVAL_NOT_REQUESTED", "The approval is no longer waiting.", "CONFLICT", {
    state: rec.state,
  });
}

// ---- policies ---------------------------------------------------------------------------------------------------

type Ver = {
  version: number;
  document: string;
  created_at: string;
  created_by: string;
  comment: string | null;
};
const V1 = `version: 1
policies:
  - id: deny-env-read
    description: Environment files hold secrets, so agents do not read them.
    when:
      capability: filesystem.read
      resource: "**/.env*"
    decision: deny
`;
const V2 = `version: 1
policies:
  - id: deny-env-read
    description: Environment files hold secrets, so agents do not read them.
    when:
      capability: filesystem.read
      resource: "**/.env*"
    decision: deny

  - id: approve-push-main
    description: A push to main needs a person to approve it.
    when:
      capability: github.push
      attributes:
        branch: main
    decision: require_approval
    approver_role: admin
    expires_in: 30m
`;
const BASE_V1 = `version: 1
default: allow
policies:
  - id: deny-catastrophic-shell
    description: Catastrophic commands are never run by an agent.
    when:
      capability: shell.execute
      risk_class: R4
    decision: deny
`;
const BASE_SCOPE = "base";
let scopes: Map<string, Ver[]> | undefined;
function policies(): Map<string, Ver[]> {
  scopes ??= new Map([
    [
      FIXTURE_PROJECT,
      [
        { version: 1, document: V1, created_at: "2026-10-05T09:00:00Z", created_by: FIXTURE_USER.id, comment: "First rule: no .env reads." },
        { version: 2, document: V2, created_at: "2026-10-06T14:30:00Z", created_by: "usr_00000000000000000000000002", comment: "Pushes to main need approval." },
        { version: 3, document: STARTER_POLICY, created_at: "2026-10-07T08:15:00Z", created_by: FIXTURE_USER.id, comment: "Add the catastrophic-command rule." },
      ],
    ],
    [BASE_SCOPE, [{ version: 1, document: BASE_V1, created_at: "2026-10-04T10:00:00Z", created_by: FIXTURE_USER.id, comment: null }]],
  ]); // prettier-ignore
  return scopes;
}

const sha = (text: string) => createHash("sha256").update(text).digest("hex");
const ruleCount = (doc: string) => (doc.match(/^\s*-\s+id:/gm) ?? []).length;
const detailOf = (v: Ver, project: string | null): VersionDetail => ({
  comment: v.comment,
  created_at: v.created_at,
  created_by: v.created_by,
  document: v.document,
  document_sha256: sha(v.document),
  project_id: project,
  rule_count: ruleCount(v.document),
  scope: project ? "project" : "workspace",
  version: v.version,
});
const outOf = (v: Ver, project: string | null) => {
  const { document: _document, ...rest } = detailOf(v, project);
  void _document;
  return rest;
};

/** The scope key for a `project_id` query value; null for an unknown project. */
function scopeFor(project: string | null): string | undefined {
  if (!project) return BASE_SCOPE;
  return project === FIXTURE_PROJECT ? project : undefined;
}

/**
 * A line-based approximation of the real validator. It reports the codes and fixed sentences for the mistakes people
 * make while editing a policy (missing or wrong version, unknown top-level key, bad decision, bad or repeated rule id,
 * tabs, an empty document). It is not the parser: the real one is exercised by the API-backed browser checks.
 */
export function fixtureValidate(doc: string): {
  valid: boolean;
  issues: Issue[];
  rule_count: number | null;
} {
  const issues: Issue[] = [];
  const add = (line: number, column: number, code: string, message: string) => {
    if (issues.length < 20) issues.push({ line, column, code, message });
  };
  const lines = doc.split("\n");
  const meaningful = lines.filter((l) => l.trim() !== "" && !l.trim().startsWith("#"));
  if (meaningful.length === 0) {
    add(1, 1, "empty_document", "The policy is empty.");
    return { valid: false, issues, rule_count: null };
  }
  const ids = new Map<string, number>();
  let hasVersion = false;
  lines.forEach((raw, i) => {
    const n = i + 1;
    if (raw.includes("\t"))
      return add(n, raw.indexOf("\t") + 1, "syntax_error", "This is not valid YAML here.");
    const top = /^([A-Za-z_][\w-]*):\s*(.*)$/.exec(raw);
    if (top) {
      const [, key, value] = top;
      if (key === "version") {
        hasVersion = true;
        if (value!.trim() !== "1")
          add(n, raw.indexOf(value!) + 1, "invalid_version", "Only 'version: 1' is supported.");
      } else if (key !== "default" && key !== "policies")
        add(n, 1, "unknown_key", `Unknown key '${key}'.`);
    }
    const id = /^(\s*-\s+id:\s*)(\S.*)$/.exec(raw);
    if (id) {
      const slug = id[2]!.trim().replace(/^["']|["']$/g, "");
      const col = id[1]!.length + 1;
      if (!/^[a-z][a-z0-9-]{0,63}$/.test(slug))
        add(n, col, "invalid_rule_id", "A rule id is a lowercase slug such as deny-env-read.");
      else if (ids.has(slug))
        add(
          n,
          col,
          "duplicate_rule_id",
          `The rule id '${slug}' is used twice (first at line ${ids.get(slug)}).`,
        );
      else ids.set(slug, n);
    }
    const dec = /^(\s+decision:\s*)(\S.*)$/.exec(raw);
    if (dec && !["allow", "deny", "require_approval"].includes(dec[2]!.trim()))
      add(
        n,
        dec[1]!.length + 1,
        "invalid_value",
        "'decision' is one of: allow, deny, require_approval.",
      );
  });
  if (!hasVersion && issues.length === 0)
    add(1, 1, "missing_key", "The policy needs 'version: 1'.");
  return {
    valid: issues.length === 0,
    issues,
    rule_count: issues.length === 0 ? ruleCount(doc) : null,
  };
}

/** Rules a draft names, in order, each with its decision and capability (line-based, like the validator). */
function draftRules(
  doc: string,
): { id: string; decision: "allow" | "deny" | "require_approval"; capability: string }[] {
  const out: { id: string; decision: "allow" | "deny" | "require_approval"; capability: string }[] =
    [];
  for (const line of doc.split("\n")) {
    const id = /^\s*-\s+id:\s*(\S.*)$/.exec(line);
    if (id)
      out.push({
        id: id[1]!.trim().replace(/^["']|["']$/g, ""),
        decision: "allow",
        capability: "",
      });
    const last = out[out.length - 1];
    const d = /^\s+decision:\s*(allow|deny|require_approval)\s*$/.exec(line);
    if (d && last) last.decision = d[1] as "allow" | "deny" | "require_approval";
    const c = /^\s+capability:\s*(\S+)\s*$/.exec(line);
    if (c && last) last.capability = c[1]!;
  }
  return out;
}

const EVENT_FOR: Record<string, string> = {
  "filesystem.read": "file.read",
  "filesystem.write": "file.modified",
  "github.push": "git.push",
  "shell.execute": "shell.command.started",
};

/** Canned, deterministic: the counts depend only on which rules the draft names. No content, as in the real response. */
function dryRun(doc: string): components["schemas"]["DryRunOut"] {
  const rules = draftRules(doc);
  const EVALUATED = 412;
  const hits = rules.map((r, i) => ({
    ...r,
    count:
      r.id === "deny-env-read"
        ? 3
        : r.id === "approve-push-main"
          ? 2
          : r.id === "deny-catastrophic-shell"
            ? 1
            : (i % 3) + 1,
  }));
  const gated = hits.filter((h) => h.decision !== "allow");
  const matched = hits.reduce((s, h) => s + h.count, 0);
  const byDecision: Record<string, number> = {
    allow: EVALUATED - gated.reduce((s, h) => s + h.count, 0),
  };
  for (const h of gated) byDecision[h.decision] = (byDecision[h.decision] ?? 0) + h.count;
  void matched;
  const samples: components["schemas"]["DryRunSample"][] = [];
  const runs = fixtureRuns();
  for (const h of gated) {
    const type = EVENT_FOR[h.capability] ?? "tool.call.started";
    let n = 0;
    for (const f of runs) {
      const e = f.events.find((x) => x.event_type === type);
      if (!e || n >= h.count || samples.length >= 25) continue;
      samples.push({
        capability: h.capability || "tool.call",
        decision: h.decision,
        event_id: e.event_id,
        risk_class: h.capability === "shell.execute" ? "R4" : null,
        rule_id: h.id,
        run_id: f.run.id,
      });
      n++;
    }
  }
  return {
    by_decision: byDecision,
    by_rule: [
      ...hits
        .filter((h) => h.decision !== "allow" || h.count > 0)
        .map((h) => ({ count: h.count, decision: h.decision, rule_id: h.id })),
      {
        count:
          byDecision["allow"]! -
          hits.filter((h) => h.decision === "allow").reduce((s, h) => s + h.count, 0),
        decision: "allow" as const,
        rule_id: null,
      },
    ],
    events_evaluated: EVALUATED,
    events_skipped: 38,
    limits: { max_events: 20000, max_runs: 100, max_samples: 25, time_budget_seconds: 5 },
    runs_considered: runs.length,
    samples,
    truncated: null,
  };
}

/** A whole-text line diff in unified form (one hunk, every line shown): enough for fixtures. */
function unified(a: string, b: string, from: number, to: number): string {
  const x = a.replace(/\n$/, "").split("\n");
  const y = b.replace(/\n$/, "").split("\n");
  const L: number[][] = Array.from({ length: x.length + 1 }, () =>
    new Array<number>(y.length + 1).fill(0),
  );
  for (let i = x.length - 1; i >= 0; i--)
    for (let j = y.length - 1; j >= 0; j--)
      L[i]![j] = x[i] === y[j] ? L[i + 1]![j + 1]! + 1 : Math.max(L[i + 1]![j]!, L[i]![j + 1]!);
  const out: string[] = [];
  let i = 0;
  let j = 0;
  while (i < x.length && j < y.length) {
    if (x[i] === y[j]) {
      out.push(` ${x[i++]}`);
      j++;
    } else if (L[i + 1]![j]! >= L[i]![j + 1]!) out.push(`-${x[i++]}`);
    else out.push(`+${y[j++]}`);
  }
  while (i < x.length) out.push(`-${x[i++]}`);
  while (j < y.length) out.push(`+${y[j++]}`);
  return (
    [`--- v${from}`, `+++ v${to}`, `@@ -1,${x.length} +1,${y.length} @@`, ...out].join("\n") + "\n"
  );
}

function currentPolicy(project: string | null): Reply {
  const scope = scopeFor(project);
  if (scope === undefined) return notFound("PROJECT_NOT_FOUND", "Project not found.");
  const mine = policies().get(scope);
  if (mine?.length)
    return {
      status: 200,
      body: {
        policy: detailOf(mine[mine.length - 1]!, project),
        project_id: project,
        source: project ? "project" : "workspace",
      },
    };
  const base = policies().get(BASE_SCOPE)!;
  return {
    status: 200,
    body: {
      policy: detailOf(base[base.length - 1]!, null),
      project_id: project,
      source: "workspace",
    },
  };
}

function policyGet(rest: string[], q: URLSearchParams): Reply {
  const project = q.get("project_id");
  const scope = scopeFor(project);
  if (scope === undefined) return notFound("PROJECT_NOT_FOUND", "Project not found.");
  const list = policies().get(scope) ?? [];
  const [what, which] = rest;
  if (what === "current" && !which) return currentPolicy(project);
  if (what === "versions" && !which) {
    const before = q.get("before_version") ? Number(q.get("before_version")) : Infinity;
    const limit = Math.min(Math.max(Number(q.get("limit") ?? 20) || 20, 1), 100);
    const older = [...list].reverse().filter((v) => v.version < before);
    const items = older.slice(0, limit);
    return {
      status: 200,
      body: {
        items: items.map((v) => outOf(v, project)),
        next_before_version: older.length > limit ? items[items.length - 1]!.version : null,
      },
    };
  }
  if (what === "versions" && which) {
    const v = list.find((x) => String(x.version) === which);
    return v
      ? { status: 200, body: detailOf(v, project) }
      : notFound("POLICY_VERSION_NOT_FOUND", "Policy version not found.");
  }
  if (what === "diff" && !which) {
    const a = list.find((x) => String(x.version) === q.get("from_version"));
    const b = list.find((x) => String(x.version) === q.get("to_version"));
    if (!a || !b) return notFound("POLICY_VERSION_NOT_FOUND", "Policy version not found.");
    return {
      status: 200,
      body: {
        diff: unified(a.document, b.document, a.version, b.version),
        from_version: a.version,
        to_version: b.version,
        project_id: project,
        truncated: false,
      },
    };
  }
  return notFound("NOT_FOUND", "Not found.");
}

function policyPost(what: string, body: unknown): Reply {
  const b = (body ?? {}) as {
    document?: unknown;
    project_id?: unknown;
    base_version?: unknown;
    comment?: unknown;
  };
  if (typeof b.document !== "string" || b.document.length > 256 * 1024)
    return err(422, "VALIDATION_ERROR", "The request is not valid.", "VALIDATION");
  const project = typeof b.project_id === "string" && b.project_id ? b.project_id : null;
  if (what === "validate") return { status: 200, body: fixtureValidate(b.document) };
  const scope = scopeFor(project);
  if (scope === undefined) return notFound("PROJECT_NOT_FOUND", "Project not found.");
  const checked = fixtureValidate(b.document);
  if (!checked.valid)
    return err(422, "POLICY_INVALID", "The policy document is not valid.", "VALIDATION", {
      issues: checked.issues,
    });
  if (what === "dry-run") {
    if (!project) return err(422, "VALIDATION_ERROR", "A dry run needs a project.", "VALIDATION");
    return { status: 200, body: dryRun(b.document) };
  }
  // publish
  const list = policies().get(scope) ?? [];
  const current = list.length ? list[list.length - 1]!.version : 0;
  if (typeof b.base_version === "number" && b.base_version !== current)
    return err(
      409,
      "POLICY_VERSION_CONFLICT",
      "The policy was changed by someone else; reload it before publishing.",
      "CONFLICT",
      { current_version: current },
    );
  const v: Ver = {
    version: current + 1,
    document: b.document,
    created_at: new Date().toISOString(),
    created_by: FIXTURE_USER.id,
    comment: typeof b.comment === "string" && b.comment ? b.comment.slice(0, 500) : null,
  };
  policies().set(scope, [...list, v]);
  return { status: 200, body: detailOf(v, project) };
}

/** Forget every change made by a write (tests; the server process otherwise keeps them until it restarts). */
export function resetPolicyFixtures(): void {
  store = undefined;
  scopes = undefined;
  T0 = undefined;
}

// ---- entry points -----------------------------------------------------------------------------------------------

export const isPolicyFixturePath = (segments: string[]): boolean =>
  segments[0] === "v1" && (segments[1] === "approvals" || segments[1] === "policies");

/** GET /v1/approvals..., /v1/policies/...; null when the path is not one of these. */
export function fixturePolicyReply(
  segments: string[],
  q: URLSearchParams,
  viewer: FixtureViewer,
): Reply | null {
  if (!isPolicyFixturePath(segments)) return null;
  if (!viewer.content) return forbidden();
  const [, area, a, b] = segments;
  if (area === "approvals") {
    if (!a) return listApprovals(q);
    if (a === "pending-count" && !b)
      return {
        status: 200,
        body: { count: [...approvals().values()].filter((r) => r.state === "REQUESTED").length },
      };
    const rec = approvals().get(a);
    return rec && !b
      ? { status: 200, body: view(rec) }
      : notFound("APPROVAL_NOT_FOUND", "Approval not found.");
  }
  return policyGet(segments.slice(2), q);
}

/** POST /v1/policies/(validate|publish|dry-run) and POST /v1/approvals/{id}/decision; null when not one of these. */
export function fixturePolicyWrite(
  segments: string[],
  body: unknown,
  viewer: FixtureViewer,
): Reply | null {
  if (!isPolicyFixturePath(segments)) return null;
  if (!viewer.content) return forbidden();
  const [, area, a, b] = segments;
  if (area === "policies" && a && !b && ["validate", "publish", "dry-run"].includes(a))
    return policyPost(a, body);
  if (area === "approvals" && a && b === "decision") return decideApproval(a, body);
  return err(405, "METHOD_NOT_ALLOWED", "Method not allowed.", "VALIDATION");
}

export { USED_APPROVAL_ID, WAITING_APPROVAL_ID };
