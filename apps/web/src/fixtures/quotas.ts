import type { components } from "@/lib/api/schema";
import { FIXTURE_PROJECT } from "./builder";
import { FIXTURE_PROJECTS } from "./auth";

/**
 * The quota routes of the fixture API (Phase 19a): per-project usage with in-memory overrides, so the Quotas page can be
 * exercised without an API. The figures are chosen to show every state: one quota well inside its limit, one near it,
 * one that has reached it. The rules (bounds, status words, percentages) mirror the API's.
 */
type Out = components["schemas"]["ProjectQuotasOut"];
type Row = components["schemas"]["QuotaUsageOut"];
type Name = Row["quota"];
type Reply = { status: number; body: unknown };

const GIB = 1024 ** 3;
const DEFAULTS: Record<Name, number> = {
  agents: 500,
  runs_per_day: 50_000,
  events_per_day: 5_000_000,
  artifact_bytes: 5 * GIB,
};
const USED: Record<Name, number> = {
  agents: 12,
  runs_per_day: 41_200,
  events_per_day: 5_000_000,
  artifact_bytes: Math.round(1.25 * GIB),
};
const BOUNDS: Record<Name, [number, number]> = {
  agents: [1, 1_000_000],
  runs_per_day: [1, 100_000_000],
  events_per_day: [1, 100_000_000_000],
  artifact_bytes: [1, 1024 ** 5],
};
const NAMES = Object.keys(DEFAULTS) as Name[];

const err = (status: number, code: string, message: string, category: string): Reply => ({
  status,
  body: {
    error: { code, message, category, retryable: false, request_id: "req_fixture", details: {} },
  },
});

type State = { overrides: Partial<Record<Name, number>>; updatedAt: string | null };
const state = new Map<string, State>();
export const resetFixtureQuotas = (): void => state.clear();

const statusOf = (used: number, limit: number): Row["status"] =>
  used >= limit ? "at_limit" : used * 100 >= limit * 80 ? "near_limit" : "ok";

function view(projectId: string): Out {
  const cur = state.get(projectId) ?? { overrides: {}, updatedAt: null };
  return {
    project_id: projectId,
    day: "2026-10-07",
    resets_at: "2026-10-08T00:00:00Z",
    enforced: true,
    quotas: NAMES.map((q) => {
      const limit = cur.overrides[q] ?? DEFAULTS[q];
      return {
        quota: q,
        period: q === "runs_per_day" || q === "events_per_day" ? "day" : "total",
        limit,
        default: DEFAULTS[q],
        overridden: cur.overrides[q] !== undefined,
        used: USED[q],
        percent: Math.floor((USED[q] * 100) / limit),
        status: statusOf(USED[q], limit),
      };
    }),
    updated_at: cur.updatedAt,
    updated_by: cur.updatedAt ? "usr_00000000000000000000000001" : null,
  };
}

/** GET (body undefined) or PUT of `/v1/projects/{id}/quotas`. Reading is open to every member; writing needs project.write. */
export function fixtureQuotas(projectId: string, body: unknown, canWrite: boolean): Reply {
  if (!FIXTURE_PROJECTS.some((p) => p.id === projectId) && projectId !== FIXTURE_PROJECT)
    return err(404, "PROJECT_NOT_FOUND", "Project not found.", "NOT_FOUND");
  if (body === undefined) return { status: 200, body: view(projectId) };
  if (!canWrite)
    return err(
      403,
      "PERMISSION_DENIED",
      "Your role cannot change project settings.",
      "AUTHORIZATION",
    );
  const given = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
  const next = { ...(state.get(projectId)?.overrides ?? {}) };
  for (const [key, value] of Object.entries(given)) {
    if (!NAMES.includes(key as Name))
      return err(422, "REQUEST_INVALID", "Unknown quota.", "VALIDATION");
    const q = key as Name;
    if (value === null) {
      delete next[q];
      continue;
    }
    const [low, high] = BOUNDS[q];
    if (typeof value !== "number" || !Number.isInteger(value) || value < low || value > high)
      return err(
        422,
        "QUOTA_SETTINGS_INVALID",
        `The ${q} quota must be a whole number from ${low} to ${high}.`,
        "VALIDATION",
      );
    next[q] = value;
  }
  state.set(projectId, { overrides: next, updatedAt: "2026-10-07T09:30:00Z" });
  return { status: 200, body: view(projectId) };
}
