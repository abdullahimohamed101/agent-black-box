import type { components } from "./api/schema";

/**
 * Presentation logic for project quotas (Phase 19a, ADR-154). The API decides every figure and every status; this file
 * only names them, formats them and checks a typed override against the same bounds the API enforces.
 *
 * Status is carried by a glyph and a word, never by a new hue and never by red (red is a failed run, ADR-111): amber
 * from 80 percent ("Near limit") and for a quota that is refusing new items ("Limit reached").
 */
export type ProjectQuotas = components["schemas"]["ProjectQuotasOut"];
export type QuotaRow = components["schemas"]["QuotaUsageOut"];
export type QuotaName = QuotaRow["quota"];
export type QuotaStatus = QuotaRow["status"];

export const QUOTA_ORDER: readonly QuotaName[] = [
  "agents",
  "runs_per_day",
  "events_per_day",
  "artifact_bytes",
];

const MIB = 1024 ** 2;

type Meta = {
  label: string;
  /** What is counted, for "12 of 500 agents". */
  noun: string;
  unit: "count" | "bytes";
  /** What happens to new items at the limit, in words (existing data always keeps working). */
  refusal: string;
  min: number;
  max: number;
};

/** The bounds mirror `abb_api.quotas.model.BOUNDS` (the API stays the authority; this saves a round trip). */
export const QUOTA_META: Record<QuotaName, Meta> = {
  agents: {
    label: "Distinct agents",
    noun: "agents",
    unit: "count",
    refusal:
      "New agent names are refused. Existing agents and their runs keep working until the limit is raised.",
    min: 1,
    max: 1_000_000,
  },
  runs_per_day: {
    label: "Runs per day",
    noun: "runs today",
    unit: "count",
    refusal:
      "Events that would start a new run are refused until midnight UTC. Runs already counted today keep working.",
    min: 1,
    max: 100_000_000,
  },
  events_per_day: {
    label: "Events per day",
    noun: "events today",
    unit: "count",
    refusal: "Events beyond the limit are refused until midnight UTC. Replays are still accepted.",
    min: 1,
    max: 100_000_000_000,
  },
  artifact_bytes: {
    label: "Artifact storage",
    noun: "stored",
    unit: "bytes",
    refusal: "New artifacts are refused. Stored artifacts stay readable.",
    min: 1,
    max: 1024 ** 5,
  },
};

export const quotaMeta = (q: string): Meta | null =>
  (QUOTA_META as Record<string, Meta | undefined>)[q] ?? null;

const STATUS: Record<QuotaStatus, { word: string; glyph: string }> = {
  ok: { word: "Within limit", glyph: "○" },
  near_limit: { word: "Near limit", glyph: "◐" },
  at_limit: { word: "Limit reached", glyph: "●" },
};
export const quotaStatus = (s: string): { word: string; glyph: string } =>
  (STATUS as Record<string, { word: string; glyph: string } | undefined>)[s] ?? STATUS.ok;

const UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"] as const;
export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  let value = n;
  let i = 0;
  while (value >= 1024 && i < UNITS.length - 1) {
    value /= 1024;
    i += 1;
  }
  const text = i === 0 ? String(Math.round(value)) : value.toFixed(value < 10 ? 2 : 1);
  return `${text.replace(/\.?0+$/, (m) => (m.startsWith(".") ? "" : m))} ${UNITS[i]}`;
}

export const formatCount = (n: number): string =>
  Number.isFinite(n) ? Math.round(n).toLocaleString("en-US") : "—";

export function formatQuantity(quota: string, n: number): string {
  return quotaMeta(quota)?.unit === "bytes" ? formatBytes(n) : formatCount(n);
}

/** The width of the usage bar, 0 to 100 (a lowered limit can put use above it). */
export const barPercent = (percent: number): number =>
  Math.max(0, Math.min(100, Number.isFinite(percent) ? percent : 0));

export function usageSentence(row: QuotaRow): string {
  const meta = quotaMeta(row.quota);
  const noun = meta ? meta.noun : "";
  return `${formatQuantity(row.quota, row.used)} of ${formatQuantity(row.quota, row.limit)} ${noun}`.trim();
}

/** What the input shows: counts as typed, storage in whole MiB. */
export function inputValue(quota: QuotaName, override: number | null): string {
  if (override === null) return "";
  return String(quotaMeta(quota)?.unit === "bytes" ? Math.round(override / MIB) : override);
}

export type Parsed = { ok: true; value: number | null } | { ok: false; message: string };

/**
 * An override typed by a person: empty means "use the default" (null). Whole numbers only; storage is typed in MiB.
 * The message never repeats what was typed.
 */
export function parseOverride(quota: QuotaName, text: string): Parsed {
  const meta = QUOTA_META[quota];
  const cleaned = text.trim().replace(/[,_\s]/g, "");
  if (cleaned === "") return { ok: true, value: null };
  const unitText = meta.unit === "bytes" ? "MiB" : "";
  const bad = (): Parsed => ({
    ok: false,
    message: `Enter a whole number${unitText ? ` of ${unitText}` : ""} from ${formatCount(
      meta.unit === "bytes" ? 1 : meta.min,
    )} to ${formatCount(meta.unit === "bytes" ? meta.max / MIB : meta.max)}, or leave it empty for the default.`,
  });
  if (!/^\d{1,15}$/.test(cleaned)) return bad();
  const n = Number(cleaned) * (meta.unit === "bytes" ? MIB : 1);
  if (!Number.isSafeInteger(n) || n < meta.min || n > meta.max) return bad();
  return { ok: true, value: n };
}
