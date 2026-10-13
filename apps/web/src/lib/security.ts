import type { components } from "./api/schema";
import type { FindingOut } from "./api/types";

/**
 * Presentation logic for security observability (Phase 13, ADR-131). A security finding states a category, a severity, a
 * count and event links with fixed wording; this file only ever reads those known fields. Nothing here (or in the
 * components that use it) renders a path, command, host or secret: the API never sends one, and the readers below ignore
 * every field they do not name, so a stray extra field cannot reach the page.
 */

export type SecuritySeverity = FindingOut["severity"];
export type SecurityRow = components["schemas"]["SecurityFindingOut"];
export type SecurityFacets = components["schemas"]["SecurityFacets"];
export type DetectorOut = components["schemas"]["DetectorOut"];
export type CaptureMode = components["schemas"]["SecuritySettingsOut"]["capture_mode"];

/**
 * Severity is carried by glyph and weight, never by a new hue (and never red: red is a failed run, ADR-111).
 * `style` picks the shield: filled for critical, outlined otherwise. `tone` is the colour role in security.css.
 */
const SEVERITY: Record<
  SecuritySeverity,
  { label: string; style: "filled" | "outlined"; tone: "ink" | "wait" | "muted"; rank: number }
> = {
  critical: { label: "Critical", style: "filled", tone: "ink", rank: 4 },
  high: { label: "High", style: "outlined", tone: "wait", rank: 3 },
  medium: { label: "Medium", style: "outlined", tone: "muted", rank: 2 },
  warning: { label: "Warning", style: "outlined", tone: "muted", rank: 1 },
  info: { label: "Info", style: "outlined", tone: "muted", rank: 0 },
};
export const SEVERITY_ORDER: readonly SecuritySeverity[] = [
  "critical",
  "high",
  "medium",
  "warning",
  "info",
];
export const securitySeverityMeta = (s: string) =>
  SEVERITY[s as SecuritySeverity] ?? {
    label: s.slice(0, 24),
    style: "outlined" as const,
    tone: "muted" as const,
    rank: -1,
  };

/** Short names for the facet column and table; the fixed sentence for a finding comes from the API. */
const DETECTOR_NAMES: Record<string, string> = {
  "sec.secret_file_read": "Secret file read",
  "sec.credential_in_output": "Credential in output",
  "sec.destructive_command": "Destructive command",
  "sec.risky_git": "Risky git",
  "sec.privileged_db": "Privileged database action",
  "sec.unexpected_destination": "Unexpected destination",
};
export const detectorName = (id: string): string => DETECTOR_NAMES[id] ?? id.slice(0, 40);

export const isSecurity = (f: Pick<FindingOut, "category">): boolean => f.category === "security";

/** Splits a run's findings: the security ones (most severe first, API order otherwise) and the reliability ones. */
export function splitFindings(items: readonly FindingOut[]): {
  security: FindingOut[];
  reliability: FindingOut[];
} {
  const security = items
    .filter(isSecurity)
    .map((f, i) => ({ f, i }))
    .sort(
      (a, b) =>
        securitySeverityMeta(b.f.severity).rank - securitySeverityMeta(a.f.severity).rank ||
        a.i - b.i,
    )
    .map(({ f }) => f);
  return { security, reliability: items.filter((f) => !isSecurity(f)) };
}

const MAX_TEXT = 240;
const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.slice(0, MAX_TEXT) : null;

/**
 * The only parts of a run-page security finding that are shown. `rule` is the API's constant sentence from
 * `evidence.rule`; everything else in `evidence` is ignored on purpose.
 */
export type SecurityLine = {
  id: string;
  severity: SecuritySeverity;
  title: string;
  rule: string | null;
  count: number | null;
  /** The event "Show evidence" opens; null for a finding with no event link (an artifact only). */
  target: string | null;
};
export function securityLine(f: FindingOut): SecurityLine {
  const e = f.evidence as Record<string, unknown> | null | undefined;
  const count =
    e && typeof e["count"] === "number" && Number.isFinite(e["count"]) ? e["count"] : null;
  return {
    id: f.id,
    severity: f.severity,
    title: f.title.slice(0, MAX_TEXT),
    rule: e ? str(e["rule"]) : null,
    count,
    target: f.start_event_id ?? f.evidence_event_ids[0] ?? f.end_event_id ?? null,
  };
}

export const securityCountText = (n: number): string =>
  `${n} security finding${n === 1 ? "" : "s"}`;

/** "2 security, 1 finding": what the closed Context button says is inside. Reliability keeps its old wording. */
export function contextSummary(security: number, reliability: number): string {
  const parts: string[] = [];
  if (security > 0) parts.push(`${security} security`);
  if (reliability > 0) parts.push(`${reliability} finding${reliability === 1 ? "" : "s"}`);
  return parts.join(", ");
}

// ------------------------------------------------------------------------------------- the workspace page's URL

export type RangeKey = "24h" | "7d" | "30d" | "all";
export const RANGE_LABEL: Record<RangeKey, string> = {
  "24h": "Last 24 hours",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  all: "All time",
};
const RANGE_MS: Record<Exclude<RangeKey, "all">, number> = {
  "24h": 86_400_000,
  "7d": 7 * 86_400_000,
  "30d": 30 * 86_400_000,
};

export type SecurityFilters = {
  severity: string[];
  detector: string[];
  /** A project slug or id as written in the URL. */
  project: string;
  range: RangeKey;
  /** Explicit bounds from a shared link (tz-aware ISO). */
  since: string;
  until: string;
};
export const NO_SECURITY_FILTERS: SecurityFilters = {
  severity: [],
  detector: [],
  project: "",
  range: "all",
  since: "",
  until: "",
};

const csv = (v: string | null): string[] =>
  v
    ? [
        ...new Set(
          v
            .split(",")
            .map((x) => x.trim())
            .filter(Boolean),
        ),
      ].slice(0, 12)
    : [];

/** A tz-aware ISO instant, or "". The API rejects naive times, so the page never forwards one. */
export function parseInstant(v: string | null): string {
  if (!v || !/(Z|[+-]\d{2}:?\d{2})$/i.test(v)) return "";
  const t = Date.parse(v);
  return Number.isNaN(t) ? "" : new Date(t).toISOString();
}

export function parseSecurityFilters(sp: URLSearchParams): SecurityFilters {
  const range = sp.get("range");
  return {
    severity: csv(sp.get("severity")).filter((s) => s in SEVERITY),
    detector: csv(sp.get("detector")).filter((d) => /^sec\.[a-z0-9_]{1,60}$/.test(d)),
    project: (sp.get("project") ?? "").slice(0, 64),
    range: range === "24h" || range === "7d" || range === "30d" ? range : "all",
    since: parseInstant(sp.get("since")),
    until: parseInstant(sp.get("until")),
  };
}

export function securityQueryString(f: SecurityFilters): string {
  const p = new URLSearchParams();
  if (f.severity.length) p.set("severity", f.severity.join(","));
  if (f.detector.length) p.set("detector", f.detector.join(","));
  if (f.project) p.set("project", f.project);
  if (f.range !== "all") p.set("range", f.range);
  if (f.since) p.set("since", f.since);
  if (f.until) p.set("until", f.until);
  return p.toString();
}

export const hasSecurityFilters = (f: SecurityFilters): boolean => securityQueryString(f) !== "";

/** Adds or removes one value of a list filter. */
export function toggleValue(list: readonly string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

/** The bounds sent to the API. A preset is resolved against `now` (rounded to the minute so the query key is stable). */
export function rangeBounds(f: SecurityFilters, now: number): { since?: string; until?: string } {
  const minute = Math.floor(now / 60_000) * 60_000;
  const since =
    f.since || (f.range !== "all" ? new Date(minute - RANGE_MS[f.range]).toISOString() : "");
  return { since: since || undefined, until: f.until || undefined };
}

export const securityPath = (workspace: string, project?: string | null): string =>
  `/w/${workspace}/security${project ? `?${new URLSearchParams({ project }).toString()}` : ""}`;

/** A deep link to a run with one security finding's evidence open (the run page reads `finding`). */
export const findingHref = (
  workspace: string,
  projectSlug: string,
  runId: string,
  findingId: string,
): string =>
  `/w/${workspace}/projects/${encodeURIComponent(projectSlug)}/runs/${runId}?${new URLSearchParams({ finding: findingId }).toString()}`;

// ------------------------------------------------------------------------------------- allowlist (mirrors the API)

export const MAX_HOST_PATTERN_LENGTH = 100;
export const MAX_ALLOWLIST_ENTRIES = 256;
const HOST_PATTERN =
  /^(?:\*\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$/;

/** The API's fixed sentence for a malformed entry (never repeats the value). */
export const HOST_PATTERN_MESSAGE =
  "An allowlist entry must be a host name such as 'api.example.com' or '*.example.com' (no scheme, port or path).";

/** Normalises like the API (trim, lower-case, no trailing dot). Returns the entry or the fixed message. */
export function normalizeHostPattern(
  raw: string,
): { ok: true; host: string } | { ok: false; message: string } {
  const host = raw.trim().toLowerCase().replace(/\.+$/, "");
  if (host.length === 0 || host.length > MAX_HOST_PATTERN_LENGTH || !HOST_PATTERN.test(host))
    return { ok: false, message: HOST_PATTERN_MESSAGE };
  return { ok: true, host };
}

export const CAPTURE_MODES: readonly {
  value: CaptureMode;
  label: string;
  hint: string;
}[] = [
  {
    value: "METADATA_ONLY",
    label: "Metadata only (default)",
    hint: "Timings, types and counts are kept. No payloads or artifact bodies are stored.",
  },
  {
    value: "FULL",
    label: "Full",
    hint: "Payloads and artifacts are stored after the server removes secret-shaped values.",
  },
  {
    value: "DISABLED",
    label: "Disabled",
    hint: "Metadata only, and no free text such as error messages.",
  },
];
