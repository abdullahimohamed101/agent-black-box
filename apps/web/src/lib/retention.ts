import { ApiRequestError } from "./api/client";
import type { components } from "./api/schema";

/**
 * Presentation logic for the Retention settings (Phase 19b, ADR-159/160). The API decides every figure; this file names
 * the tiers, turns a form draft into the request body the API accepts, finds which tiers a draft would shorten (the
 * save asks for confirmation then: deletion is permanent) and puts errors into words that never repeat a value.
 */
export type RetentionPolicy = components["schemas"]["RetentionPolicyOut"];
export type TierPolicy = components["schemas"]["TierPolicyOut"];
export type TierName = TierPolicy["tier"];
export type RetentionMode = RetentionPolicy["mode"];
export type RetentionPreview = components["schemas"]["RetentionPreviewOut"];
export type PreviewTier = components["schemas"]["PreviewTierOut"];
export type RetentionReport = components["schemas"]["RetentionReportOut"];
export type RetentionPolicyBody = components["schemas"]["RetentionPolicyIn"];

export const TIER_ORDER: readonly TierName[] = ["artifacts", "trace", "run", "audit"];
/** The API refuses more than this (`RETENTION_CEILING_DAYS`). */
export const CEILING_DAYS = 36_500;

type TierMeta = { label: string; removes: string };

/** What each tier removes, in the words of ADR-159/160. The run record is deleted last. */
export const TIER_META: Record<TierName, TierMeta> = {
  artifacts: {
    label: "Artifacts",
    removes:
      "Terminal output and large payload bodies stored as files. The events and the run page stay.",
  },
  trace: {
    label: "Trace detail",
    removes:
      "Events, spans, findings and evaluations of a run, and its search entry. The run summary and its numbers stay; the run page says the trace expired.",
  },
  run: {
    label: "Run record",
    removes:
      "The run summary and its numbers, and the run's share of the analytics. The run no longer exists afterwards.",
  },
  audit: {
    label: "Audit log",
    removes:
      "Audit log entries older than this. Each removal batch leaves a summary entry with counts only.",
  },
};

export type Choice = "default" | "days" | "forever";
export type Draft = { choice: Choice; days: string };
export type Drafts = Record<TierName, Draft>;

export const tierOf = (policy: RetentionPolicy, name: TierName): TierPolicy | undefined =>
  policy.tiers.find((t) => t.tier === name);

export function draftOf(tier: TierPolicy | undefined): Draft {
  if (!tier || tier.source === "default") return { choice: "default", days: "" };
  return tier.forever || tier.days === null
    ? { choice: "forever", days: "" }
    : { choice: "days", days: String(tier.days) };
}

export const draftsOf = (policy: RetentionPolicy): Drafts => ({
  artifacts: draftOf(tierOf(policy, "artifacts")),
  trace: draftOf(tierOf(policy, "trace")),
  run: draftOf(tierOf(policy, "run")),
  audit: draftOf(tierOf(policy, "audit")),
});

export const describeDays = (days: number | null): string =>
  days === null ? "Keep forever" : `${days} day${days === 1 ? "" : "s"}`;

/** What a tier currently says, with where the value comes from. */
export function describeTier(tier: TierPolicy): string {
  return `${describeDays(tier.forever ? null : tier.days)} (${
    tier.source === "workspace" ? "set for this workspace" : "installation default"
  })`;
}

export type Parsed =
  { ok: true; setting: number | "forever" | null } | { ok: false; message: string };

/** The stored setting a draft means: `null` is the installation default. The messages never echo what was typed. */
export function parseDraft(draft: Draft, floor: number): Parsed {
  if (draft.choice === "default") return { ok: true, setting: null };
  if (draft.choice === "forever") return { ok: true, setting: "forever" };
  const text = draft.days.trim();
  if (!/^[0-9]{1,6}$/.test(text))
    return { ok: false, message: "Enter a whole number of days, or choose Keep forever." };
  const n = Number(text);
  if (n < floor) return { ok: false, message: `Keep data for at least ${floor} days.` };
  if (n > CEILING_DAYS)
    return {
      ok: false,
      message: `That is longer than the permitted maximum of ${CEILING_DAYS} days.`,
    };
  return { ok: true, setting: n };
}

/** Effective days of a setting (`null`: forever). */
function effectiveOf(tier: TierPolicy, setting: number | "forever" | null): number | null {
  if (setting === null) return tier.default_days;
  return setting === "forever" ? null : setting;
}

export type Proposal =
  | { ok: true; effective: Record<TierName, number | null> }
  | { ok: false; errors: Partial<Record<TierName, string>> };

/**
 * The effective days a set of drafts means, or the per-tier problems. Also checks the order the API enforces: the
 * run record is deleted last, so artifacts cannot outlast trace detail nor trace detail the run record.
 */
export function propose(policy: RetentionPolicy, drafts: Drafts): Proposal {
  const errors: Partial<Record<TierName, string>> = {};
  const effective = {} as Record<TierName, number | null>;
  for (const name of TIER_ORDER) {
    const tier = tierOf(policy, name);
    const parsed = parseDraft(drafts[name], policy.floor_days);
    if (!parsed.ok) errors[name] = parsed.message;
    else if (tier) effective[name] = effectiveOf(tier, parsed.setting);
  }
  if (Object.keys(errors).length === 0) {
    for (const [inner, outer] of [
      ["artifacts", "trace"],
      ["trace", "run"],
    ] as const) {
      const shorter = effective[inner];
      const longer = effective[outer];
      if (longer !== null && (shorter === null || shorter > longer))
        errors[inner] =
          `${TIER_META[inner].label} cannot be kept longer than ${TIER_META[outer].label.toLowerCase()}: the run record is deleted last.`;
    }
  }
  return Object.keys(errors).length ? { ok: false, errors } : { ok: true, effective };
}

const sameAsStored = (policy: RetentionPolicy, name: TierName, now: Draft): boolean => {
  const stored = draftOf(tierOf(policy, name));
  return stored.choice === now.choice && (now.choice !== "days" || stored.days === now.days.trim());
};

/** Whether any tier's draft differs from what is stored (an invalid draft counts: saving it shows why it is refused). */
export const isDirty = (policy: RetentionPolicy, drafts: Drafts): boolean =>
  TIER_ORDER.some((name) => !sameAsStored(policy, name, drafts[name]));

/** Only the tiers whose draft differs from what is stored go over the wire (a tier left out stays as it is). */
export function changedBody(policy: RetentionPolicy, drafts: Drafts): RetentionPolicyBody {
  const body: RetentionPolicyBody = {};
  for (const name of TIER_ORDER) {
    if (sameAsStored(policy, name, drafts[name])) continue;
    const parsed = parseDraft(drafts[name], policy.floor_days);
    if (parsed.ok) body[name] = parsed.setting;
  }
  return body;
}

/** Tiers whose effective period gets shorter than today's (data older than the new period would be deleted). */
export function shortened(policy: RetentionPolicy, effective: Record<TierName, number | null>) {
  return TIER_ORDER.filter((name) => {
    const tier = tierOf(policy, name);
    if (!tier) return false;
    const now = tier.forever ? null : tier.days;
    const next = effective[name];
    if (next === null) return false;
    return now === null || next < now;
  });
}

/** The query of a preview for a proposal: every tier with its effective value (`forever` or whole days). */
export function previewParams(effective: Record<TierName, number | null>) {
  const value = (d: number | null) => (d === null ? "forever" : String(d));
  return {
    artifacts_days: value(effective.artifacts),
    trace_days: value(effective.trace),
    run_days: value(effective.run),
    audit_days: value(effective.audit),
  };
}

export const MODE_BANNER: Record<RetentionMode, { glyph: string; title: string; detail: string }> =
  {
    dry_run: {
      glyph: "◌",
      title: "Retention is in dry-run mode: nothing is being deleted",
      detail:
        "The retention job only measures what it would delete and reports it below. An operator switches it to enforcing when the numbers look right.",
    },
    enforce: {
      glyph: "●",
      title: "Retention is enforcing",
      detail:
        "Data older than these periods is deleted for good, a run at a time. A deleted trace cannot be brought back.",
    },
    off: {
      glyph: "⏸",
      title: "Retention is off",
      detail:
        "The retention job does nothing on this installation: nothing is measured or deleted.",
    },
  };

export const MODE_WORD: Record<RetentionReport["mode"], string> = {
  dry_run: "Dry run",
  enforce: "Enforced",
};
export const MODE_WORD_ALL: Record<RetentionMode, string> = {
  dry_run: "Dry run",
  enforce: "Enforced",
  off: "Off",
};

export const REPORT_TIER_LABEL: Record<RetentionReport["tier"], string> = {
  artifacts: "Artifacts",
  trace: "Trace detail",
  run: "Run record",
  audit: "Audit log",
  operational: "Operational clean-up",
  platform: "Installation clean-up",
};

const UNITS = ["B", "KiB", "MiB", "GiB", "TiB"] as const;
export function formatBytes(bytes: number): string {
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const text = unit === 0 ? String(value) : (Math.round(value * 10) / 10).toString();
  return `${text} ${UNITS[unit]}`;
}

const n = (value: number) => value.toLocaleString("en-US");
const plural = (count: number, one: string, many = `${one}s`) =>
  `${n(count)} ${count === 1 ? one : many}`;

/** "This would remove 12 runs, 340 events and 1.5 MiB of artifacts" for one tier of a preview. */
export function previewSentence(t: PreviewTier): string {
  const bound = t.truncated ? "at least " : "";
  if (t.forever) return "Kept forever: nothing would be removed.";
  const parts: string[] = [];
  if (t.tier === "audit") parts.push(plural(t.rows, "audit entry", "audit entries"));
  else {
    parts.push(plural(t.runs, "run"));
    parts.push(plural(t.events, "event"));
    parts.push(`${formatBytes(t.artifact_bytes)} of artifacts`);
  }
  const last = parts.pop()!;
  return `This would remove ${bound}${parts.length ? `${parts.join(", ")} and ` : ""}${last}.`;
}

/** The whole preview in one sentence (the largest tier counts: tiers are measured separately, not added). */
export function previewSummary(tiers: readonly PreviewTier[]): string {
  const runs = Math.max(0, ...tiers.filter((t) => t.tier !== "audit").map((t) => t.runs));
  const events = Math.max(0, ...tiers.map((t) => t.events));
  const bytes = Math.max(0, ...tiers.map((t) => t.artifact_bytes));
  const bound = tiers.some((t) => t.truncated) ? "at least " : "";
  return `This would remove ${bound}${plural(runs, "run")}, ${plural(events, "event")} and ${formatBytes(bytes)} of artifacts.`;
}

/** One report line: what the pass found (dry run) or removed (enforced). */
export function reportCounts(r: RetentionReport): string {
  const verb = r.mode === "dry_run" ? "would remove" : "removed";
  if (r.tier === "audit") return `${verb} ${plural(r.rows, "entry", "entries")}`;
  return `${verb} ${plural(r.runs, "run")}, ${plural(r.events, "event")}, ${formatBytes(r.artifact_bytes)} of artifacts, ${plural(r.rows, "other row")}`;
}

/** An error in words. Policy and range errors come from the API with no value in them. */
export function retentionError(error: unknown): { text: string; retry: boolean } {
  if (!(error instanceof ApiRequestError)) return { text: "The request failed.", retry: false };
  if (error.status === 429 || error.code === "RATE_LIMITED") {
    const wait = error.retryAfter;
    return {
      text: `Too many requests for this workspace. ${wait !== null ? `Try again in ${wait} second${wait === 1 ? "" : "s"}.` : "Try again shortly."}`,
      retry: true,
    };
  }
  if (error.status === 504 || error.code === "RETENTION_PREVIEW_TIMEOUT")
    return {
      text: "The preview took too long. Try again later, or preview a shorter period.",
      retry: true,
    };
  if (error.status === 404)
    return { text: "This workspace was not found, or is not yours.", retry: false };
  if (error.status === 403)
    return { text: "Your role does not allow changing the retention policy.", retry: false };
  if (error.code === "RETENTION_POLICY_INVALID") return { text: error.message, retry: false };
  return { text: error.message, retry: error.retryable };
}
