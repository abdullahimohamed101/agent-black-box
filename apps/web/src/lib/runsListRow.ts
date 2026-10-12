import type { RunOut } from "@/lib/api/types";
import { formatCost } from "./format";
import { missingFirstErrorText } from "./timeline";

/**
 * What the runs list says under a run that needs attention (Phase 22 D8). It is the same opening as the run page's
 * headline ("Failed at step 7"); the list has no events, so it cannot name the tool or error type and stops at the
 * step, adding the stored cost of the failure (ADR-112). Everything here is event metadata, so a viewer reads the
 * same sentence as an owner.
 */

/** The statuses whose rows get a second line, and the word that opens it. */
const FAILURE_WORD: Partial<Record<RunOut["status"], string>> = {
  FAILED: "Failed",
  TIMED_OUT: "Timed out",
  BLOCKED: "Blocked",
};

/** The "Needs attention" status set: a failure never needs a second look to find. */
export const ATTENTION_STATUSES = [
  "FAILED",
  "TIMED_OUT",
  "BLOCKED",
] as const satisfies readonly RunOut["status"][];

export const needsAttention = (run: RunOut): boolean => run.status in FAILURE_WORD;

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

export type FailureLine = { sentence: string; detail: string };

/** `null` for a run that is not failed, timed out or blocked: it stays one quiet line. */
export function failureLine(run: RunOut): FailureLine | null {
  const word = FAILURE_WORD[run.status];
  if (!word) return null;
  const f = run.summary_state === "current" ? (run.first_error ?? null) : null;
  if (!f) return { sentence: word, detail: missingFirstErrorText(run) };
  const after = [
    f.retries_after > 0 ? plural(f.retries_after, "retry", "retries") : "no retries",
    f.cost_after_usd > 0 ? `${formatCost(f.cost_after_usd)} spent after` : null,
  ].filter(Boolean);
  return {
    sentence: f.sequence == null ? `${word} at the first error` : `${word} at step ${f.sequence}`,
    detail: after.join(" · "),
  };
}
