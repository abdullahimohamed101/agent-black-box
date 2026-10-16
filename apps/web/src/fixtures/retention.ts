import type { components } from "@/lib/api/schema";

/**
 * The retention routes of the fixture API (Phase 19b): a workspace's policy with in-memory edits, a preview computed over a
 * small invented history, and a few reports. The rules (floor, ceiling, order, malformed values) mirror the API's so the
 * page can be exercised without one. The installation mode is `dry_run`, as it is by default.
 */
type Policy = components["schemas"]["RetentionPolicyOut"];
type Tier = Policy["tiers"][number];
type Name = Tier["tier"];
type Preview = components["schemas"]["RetentionPreviewOut"];
type Reports = components["schemas"]["RetentionReportsOut"];
type Reply = { status: number; body: unknown };
type Setting = number | "forever";

const NAMES: readonly Name[] = ["artifacts", "trace", "run", "audit"];
const DEFAULTS: Record<Name, number> = { artifacts: 30, trace: 90, run: 365, audit: 365 };
const FLOOR = 7;
const CEILING = 36_500;
const MIB = 1024 ** 2;
const AT = "2026-10-15T09:30:00Z";

const err = (status: number, code: string, message: string, category: string): Reply => ({
  status,
  body: {
    error: { code, message, category, retryable: false, request_id: "req_fixture", details: {} },
  },
});
const invalid = (message: string): Reply =>
  err(422, "RETENTION_POLICY_INVALID", message, "VALIDATION");

type State = { stored: Partial<Record<Name, Setting>>; updatedAt: string | null };
const state = new Map<string, State>();
export const resetFixtureRetention = (): void => state.clear();

const days = (cur: State, n: Name): number | null => {
  const s = cur.stored[n];
  return s === undefined ? DEFAULTS[n] : s === "forever" ? null : s;
};

function view(wsId: string): Policy {
  const cur = state.get(wsId) ?? { stored: {}, updatedAt: null };
  return {
    tiers: NAMES.map((n) => {
      const d = days(cur, n);
      return {
        tier: n,
        days: d,
        forever: d === null,
        source: cur.stored[n] === undefined ? "default" : "workspace",
        default_days: DEFAULTS[n],
      };
    }),
    mode: "dry_run",
    floor_days: FLOOR,
    updated_at: cur.updatedAt,
    updated_by: cur.updatedAt ? "usr_00000000000000000000000001" : null,
  };
}

/** The invented history: 240 daily runs of 12 events and 3 MiB of artifacts each; 5 audit entries a day for 400 days. */
function measure(name: Name, d: number | null): Preview["tiers"][number] {
  const aged = (limit: number) => (d === null ? 0 : Math.max(0, limit - d));
  const runs = name === "audit" ? 0 : aged(240);
  const rows = name === "audit" ? aged(400) * 5 : runs * 5;
  return {
    tier: name,
    days: d,
    forever: d === null,
    cutoff: d === null ? null : new Date(Date.parse(AT) - d * 86_400_000).toISOString(),
    runs,
    events: runs * 12,
    artifact_bytes: name === "audit" ? 0 : runs * 3 * MIB,
    rows,
    truncated: false,
  };
}

function checkOrder(effective: Record<Name, number | null>): string | null {
  for (const [inner, outer] of [
    ["artifacts", "trace"],
    ["trace", "run"],
  ] as const) {
    const shorter = effective[inner];
    const longer = effective[outer];
    if (longer !== null && (shorter === null || shorter > longer))
      return "Artifacts cannot be kept longer than trace detail, nor trace detail longer than the run record: the run record is deleted last.";
  }
  return null;
}

function parseQuery(q: URLSearchParams, cur: State): Record<Name, number | null> | Reply {
  const out = {} as Record<Name, number | null>;
  for (const n of NAMES) {
    const raw = q.get(`${n === "artifacts" ? "artifacts" : n}_days`);
    if (raw === null) out[n] = days(cur, n);
    else if (raw === "forever") out[n] = null;
    else if (/^[0-9]{1,6}$/.test(raw)) out[n] = Number(raw);
    else return invalid("A retention value must be a whole number of days or 'forever'.");
  }
  return out;
}

const REPORTS: Reports["items"] = [
  { id: 4, tier: "trace", mode: "dry_run", runs: 18, events: 216, artifact_bytes: 0, rows: 90, truncated: false, cutoff_at: "2026-07-17T09:00:00Z", started_at: "2026-10-15T03:00:00Z", finished_at: "2026-10-15T03:00:04Z" },
  { id: 3, tier: "artifacts", mode: "dry_run", runs: 210, events: 0, artifact_bytes: 660 * MIB, rows: 630, truncated: true, cutoff_at: "2026-09-15T09:00:00Z", started_at: "2026-10-15T03:00:00Z", finished_at: "2026-10-15T03:00:02Z" },
  { id: 2, tier: "run", mode: "dry_run", runs: 0, events: 0, artifact_bytes: 0, rows: 0, truncated: false, cutoff_at: "2025-10-15T09:00:00Z", started_at: "2026-10-14T03:00:00Z", finished_at: "2026-10-14T03:00:01Z" },
  { id: 1, tier: "audit", mode: "dry_run", runs: 0, events: 0, artifact_bytes: 0, rows: 175, truncated: false, cutoff_at: "2025-10-15T09:00:00Z", started_at: "2026-10-14T03:00:00Z", finished_at: "2026-10-14T03:00:01Z" },
]; // prettier-ignore

/**
 * `GET /v1/workspaces/{id}/retention[/preview|/reports]` (`body` undefined) or the `PUT`. Reading is open to every member;
 * writing needs `retention.write`.
 */
export function fixtureRetention(
  wsId: string,
  rest: string | undefined,
  q: URLSearchParams,
  body: unknown,
  canWrite: boolean,
): Reply {
  const cur = state.get(wsId) ?? { stored: {}, updatedAt: null };
  if (rest === "reports") return { status: 200, body: { items: REPORTS } satisfies Reports };
  if (rest === "preview") {
    const eff = parseQuery(q, cur);
    if ("status" in eff) return eff;
    const order = checkOrder(eff);
    if (order) return invalid(order);
    const wanted = (q.get("tiers") ?? NAMES.join(","))
      .split(",")
      .filter((n): n is Name => NAMES.includes(n as Name));
    const out: Preview = {
      as_of: AT,
      proposed: NAMES.some((n) => q.get(`${n}_days`) !== null),
      tiers: wanted.map((n) => measure(n, eff[n])),
      note: "Nothing is deleted by a preview. Tiers are counted separately, not added.",
    };
    return { status: 200, body: out };
  }
  if (rest !== undefined) return err(404, "NOT_FOUND", "Not found.", "NOT_FOUND");
  if (body === undefined) return { status: 200, body: view(wsId) };
  if (!canWrite)
    return err(
      403,
      "PERMISSION_DENIED",
      "Your role cannot change the retention policy.",
      "AUTHORIZATION",
    );
  const given = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
  const next = { ...cur.stored };
  for (const [key, value] of Object.entries(given)) {
    if (!NAMES.includes(key as Name))
      return err(422, "REQUEST_INVALID", "Unknown tier.", "VALIDATION");
    const n = key as Name;
    if (value === null) delete next[n];
    else if (value === "forever") next[n] = "forever";
    else if (typeof value === "number" && Number.isInteger(value)) {
      if (value < FLOOR) return invalid(`Retention must be at least ${FLOOR} days (or forever).`);
      if (value > CEILING) return invalid("Retention is longer than the permitted maximum.");
      next[n] = value;
    } else return invalid("A retention value must be a whole number of days or 'forever'.");
  }
  const merged: State = { stored: next, updatedAt: cur.updatedAt };
  const order = checkOrder(
    Object.fromEntries(NAMES.map((n) => [n, days(merged, n)])) as Record<Name, number | null>,
  );
  if (order) return invalid(order);
  state.set(wsId, { stored: next, updatedAt: AT });
  return { status: 200, body: view(wsId) };
}
