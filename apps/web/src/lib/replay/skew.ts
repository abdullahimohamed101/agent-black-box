import type { EventOut, RunOut } from "@/lib/api/types";
import { deriveSpans } from "../spans";
import { buildTree, runBounds } from "../waterfall";
import { eventTimes } from "./times";

/**
 * Whether the run's clocks disagree, so that "timing across agents is approximate" (Phase 12, D6). Reuses what Phase 9
 * already flags: a span whose timestamps fall outside its parent's (the waterfall's `skewed` flag, ADR-071). On top of
 * that, canonical order itself running backwards in time between two different agents is skew too (the sequence says
 * one thing, the clocks another), and that needs no span relationship to show up.
 */
export function replayClockSkewed(
  run: Pick<RunOut, "started_at" | "completed_at" | "summary">,
  events: readonly EventOut[],
): boolean {
  const t = eventTimes(events);
  for (let i = 1; i < events.length; i++) {
    if (t[i]! < t[i - 1]! && events[i]!.agent_id !== events[i - 1]!.agent_id) return true;
  }
  let t0 = Infinity;
  for (const x of t) if (x < t0) t0 = x;
  const spans = [...deriveSpans(events).values()];
  const tree = buildTree(spans, runBounds(run, Number.isFinite(t0) ? t0 : 0));
  for (const n of tree.nodes.values()) if (n.flags.skewed) return true;
  return false;
}
