import type { components } from "@/lib/api/schema";
import { FIXTURE_PROJECT } from "./builder";
import { FIXTURE_PROJECTS } from "./auth";
import { fixtureRuns } from "./index";

/**
 * The security routes of the fixture API (Phase 13): the workspace findings list with facets and cursor paging, the
 * detector catalogue, and per-project settings with in-memory state so the settings page can be exercised without an API.
 * Rows are derived from the fixture runs' own security findings and carry the same constant wording, never content.
 */
type Row = components["schemas"]["SecurityFindingOut"];
type Detector = components["schemas"]["DetectorOut"];
type Settings = components["schemas"]["SecuritySettingsOut"];
type Reply = { status: number; body: unknown };

const err = (status: number, code: string, message: string, category: string): Reply => ({
  status,
  body: {
    error: { code, message, category, retryable: false, request_id: "req_fixture", details: {} },
  },
});

const DETECTORS: Omit<Detector, "muted" | "active">[] = [
  {
    id: "sec.secret_file_read",
    title: "Secret file read",
    description:
      "A file event or a shell command touched a file named like a secret store: .env and .env.* (not .env.example), private keys and certificates, cloud and registry credential files, kubeconfig, secrets.*. Decided from the file name only.",
    severities: ["high"],
    version: "1.0.0",
  },
  {
    id: "sec.credential_in_output",
    title: "Credential in output",
    description:
      "The server's scan, or the SDK's, replaced a provider-shaped credential (an AWS-style key, a private key, a token) in an event or an artifact. States the kind, never the value.",
    severities: ["critical"],
    version: "1.0.0",
  },
  {
    id: "sec.destructive_command",
    title: "Destructive command",
    description:
      "A shell command was classified R4, or the server's re-check found an unambiguous destructive pattern: recursive delete of a root, home or wildcard target, mkfs, dd to a device, DROP DATABASE, git clean -fdx. R3 is high, R4 is critical.",
    severities: ["high", "critical"],
    version: "1.0.0",
  },
  {
    id: "sec.risky_git",
    title: "Risky git operation",
    description:
      "A force push, a push to or a delete of main, master, production or release/*, or a history rewrite (filter-branch, reset --hard to an earlier commit, rebase -i), in a shell command or a git event.",
    severities: ["high"],
    version: "1.0.0",
  },
  {
    id: "sec.privileged_db",
    title: "Privileged database action",
    description:
      "SQL visible in a database event or passed to a database client: CREATE, ALTER, DROP, TRUNCATE, or DELETE or UPDATE with no WHERE clause.",
    severities: ["high"],
    version: "1.0.0",
  },
  {
    id: "sec.unexpected_destination",
    title: "Unexpected destination",
    description:
      "A URL or a network command named a host that matches no entry of the project's destination allowlist. Reports nothing while the allowlist is empty; localhost is always allowed.",
    severities: ["medium"],
    version: "1.0.0",
  },
];

const state = new Map<string, Settings>();
const defaults = (projectId: string): Settings => ({
  project_id: projectId,
  capture_mode: "METADATA_ONLY",
  muted_detectors: [],
  destination_allowlist: ["api.example.com"],
  detectors: [],
  updated_at: null,
  updated_by: null,
});
const settingsOf = (projectId: string): Settings => {
  let s = state.get(projectId);
  if (!s) {
    s = defaults(projectId);
    state.set(projectId, s);
  }
  return s;
};
/** Test hook: back to the defaults. */
export const resetFixtureSecuritySettings = () => state.clear();

function detectorsFor(projectId: string | null): Detector[] {
  const s = projectId ? settingsOf(projectId) : null;
  return DETECTORS.map((d) => {
    const muted = !!s?.muted_detectors.includes(d.id);
    const needsList = d.id === "sec.unexpected_destination";
    return {
      ...d,
      muted,
      active: s
        ? !muted && (!needsList || s.destination_allowlist.length > 0)
        : needsList
          ? null
          : true,
    };
  });
}

function rows(): Row[] {
  const out: Row[] = [];
  for (const f of fixtureRuns()) {
    for (const x of f.findings?.items ?? []) {
      if (x.category !== "security") continue;
      const p = FIXTURE_PROJECTS.find((q) => q.id === f.run.project_id)!;
      out.push({
        id: x.id,
        run_id: f.run.id,
        run_name: f.run.name,
        run_status: f.run.status,
        run_started_at: f.run.started_at,
        project_id: p.id,
        project_slug: p.slug,
        project_name: p.name,
        detector: x.detector,
        detector_version: x.detector_version,
        finding_type: x.finding_type,
        kind: "probable",
        severity: x.severity,
        title: x.title,
        message: x.message,
        rule: typeof x.evidence["rule"] === "string" ? x.evidence["rule"] : null,
        count: typeof x.evidence["count"] === "number" ? x.evidence["count"] : null,
        evidence_event_ids: x.evidence_event_ids,
        start_event_id: x.start_event_id,
        detected_at: x.created_at,
        updated_at: x.updated_at,
      });
    }
  }
  // Newest run first, then by id, like the API's keyset order.
  return out.sort(
    (a, b) => b.run_started_at.localeCompare(a.run_started_at) || b.id.localeCompare(a.id),
  );
}

const enc = (o: number) => Buffer.from(`o:${o}`).toString("base64url");
const dec = (c: string | null): number | null => {
  if (!c) return 0;
  const m = /^o:(\d+)$/.exec(Buffer.from(c, "base64url").toString());
  return m ? Number(m[1]) : null;
};
const instant = (v: string | null): number | null => {
  if (!v) return null;
  const t = Date.parse(v);
  return Number.isNaN(t) ? null : t;
};

function count(
  list: Row[],
  key: (r: Row) => string,
): { value: string; label: string | null; count: number }[] {
  const m = new Map<string, number>();
  for (const r of list) m.set(key(r), (m.get(key(r)) ?? 0) + 1);
  return [...m.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([value, n]) => ({ value, label: null, count: n }));
}

export function fixtureSecurityList(q: URLSearchParams, viewerCanRead = true): Reply {
  if (!viewerCanRead)
    return err(403, "PERMISSION_DENIED", "Your role cannot read runs.", "AUTHORIZATION");
  const offset = dec(q.get("cursor"));
  if (offset === null) return err(400, "CURSOR_INVALID", "Malformed cursor.", "VALIDATION");
  const limit = Math.min(Math.max(Number(q.get("limit") ?? 50) || 50, 1), 200);
  const since = instant(q.get("since"));
  const until = instant(q.get("until"));
  const f = {
    severity: q.getAll("severity"),
    detector: q.getAll("detector"),
    project: q.get("project_id"),
  };
  const all = rows();
  const within = (r: Row) => {
    const t = Date.parse(r.run_started_at);
    return (since == null || t >= since) && (until == null || t <= until);
  };
  const pass = (r: Row, skip?: "severity" | "detector" | "project") =>
    within(r) &&
    (skip === "severity" || !f.severity.length || f.severity.includes(r.severity)) &&
    (skip === "detector" || !f.detector.length || f.detector.includes(r.detector)) &&
    (skip === "project" || !f.project || f.project === r.project_id);
  const list = all.filter((r) => pass(r));
  const projects = count(
    all.filter((r) => pass(r, "project")),
    (r) => r.project_id,
  ).map((v) => ({
    ...v,
    label: FIXTURE_PROJECTS.find((p) => p.id === v.value)?.name ?? null,
  }));
  return {
    status: 200,
    body: {
      items: list.slice(offset, offset + limit),
      next_cursor: offset + limit < list.length ? enc(offset + limit) : null,
      facets: {
        severity: count(
          all.filter((r) => pass(r, "severity")),
          (r) => r.severity,
        ),
        detector: count(
          all.filter((r) => pass(r, "detector")),
          (r) => r.detector,
        ),
        project: projects,
      },
    },
  };
}

export function fixtureSecurityDetectors(q: URLSearchParams): Reply {
  const p = q.get("project_id");
  return {
    status: 200,
    body: { items: detectorsFor(p && FIXTURE_PROJECTS.some((x) => x.id === p) ? p : null) },
  };
}

const HOST =
  /^(?:\*\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$/;

/** GET (body undefined) or PUT of `/v1/projects/{id}/security-settings`. Only people who may write projects reach it. */
export function fixtureSecuritySettings(
  projectId: string,
  body: unknown,
  canWrite: boolean,
): Reply {
  if (!canWrite)
    return err(
      403,
      "PERMISSION_DENIED",
      "Your role cannot change project settings.",
      "AUTHORIZATION",
    );
  if (!FIXTURE_PROJECTS.some((p) => p.id === projectId) && projectId !== FIXTURE_PROJECT)
    return err(404, "PROJECT_NOT_FOUND", "Project not found.", "NOT_FOUND");
  const cur = settingsOf(projectId);
  if (body !== undefined) {
    const b = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
    const next = { ...cur };
    if (b["capture_mode"] != null) {
      if (!["FULL", "METADATA_ONLY", "DISABLED"].includes(String(b["capture_mode"])))
        return err(
          422,
          "SETTINGS_INVALID",
          "capture_mode must be FULL, METADATA_ONLY or DISABLED.",
          "VALIDATION",
        );
      next.capture_mode = b["capture_mode"] as Settings["capture_mode"];
    }
    if (Array.isArray(b["destination_allowlist"])) {
      const hosts = [
        ...new Set(
          (b["destination_allowlist"] as unknown[]).map((v) =>
            String(v).trim().toLowerCase().replace(/\.+$/, ""),
          ),
        ),
      ].sort();
      if (hosts.length > 256)
        return err(422, "SETTINGS_INVALID", "at most 256 allowlist entries", "VALIDATION");
      if (hosts.some((h) => h.length > 100 || !HOST.test(h)))
        return err(
          422,
          "SETTINGS_INVALID",
          "an allowlist entry must be a host name such as 'api.example.com' or '*.example.com' (no scheme, port or path)",
          "VALIDATION",
        );
      next.destination_allowlist = hosts;
    }
    if (Array.isArray(b["muted_detectors"])) {
      const ids = [...new Set((b["muted_detectors"] as unknown[]).map(String))].sort();
      if (!ids.every((i) => DETECTORS.some((d) => d.id === i)))
        return err(
          422,
          "SETTINGS_INVALID",
          "a detector id must look like 'sec.secret_file_read'",
          "VALIDATION",
        );
      next.muted_detectors = ids;
    }
    next.updated_at = "2026-10-13T09:00:00Z";
    next.updated_by = "usr_00000000000000000000000001";
    state.set(projectId, next);
  }
  const s = settingsOf(projectId);
  return { status: 200, body: { ...s, detectors: detectorsFor(projectId) } };
}
