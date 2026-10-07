import { isActive, type RunOut } from "@/lib/api/types";
import { num } from "./format";

export type DashboardStats = {
  sampleSize: number;
  finished: number;
  succeeded: number;
  successRate: number | null;
  failures: number;
  totalCostUsd: number;
  avgDurationMs: number | null;
  activeRuns: number;
  activeAgents: string[];
  recent: RunOut[];
  recentFailures: RunOut[];
};

const FAILED = new Set(["FAILED", "TIMED_OUT", "BLOCKED"]);

/**
 * KI-028: no aggregate endpoint exists yet, so this is computed over the runs the caller fetched
 * (newest first) and the UI labels it as a sample. Phase 7 replaces it with server-side analytics.
 */
export function computeDashboard(runs: readonly RunOut[]): DashboardStats {
  const finished = runs.filter((r) => !isActive(r.status) && r.status !== "CANCELLED");
  const succeeded = finished.filter((r) => r.status === "SUCCESS").length;
  const durations = finished.map((r) => r.duration_ms).filter((d): d is number => d != null);
  const active = runs.filter((r) => isActive(r.status));
  return {
    sampleSize: runs.length,
    finished: finished.length,
    succeeded,
    successRate: finished.length ? succeeded / finished.length : null,
    failures: runs.filter((r) => FAILED.has(r.status)).length,
    totalCostUsd: runs.reduce((s, r) => s + (num(r.summary["estimated_cost_usd"]) ?? 0), 0),
    avgDurationMs: durations.length
      ? durations.reduce((a, b) => a + b, 0) / durations.length
      : null,
    activeRuns: active.length,
    activeAgents: [
      ...new Set(active.map((r) => r.agent_id).filter((a): a is string => !!a)),
    ].sort(),
    recent: runs.slice(0, 8),
    recentFailures: runs.filter((r) => FAILED.has(r.status)).slice(0, 5),
  };
}
