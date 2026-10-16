import type { RunOut } from "./api/types";

/**
 * A run's trace detail (events, spans, findings, evaluations) is removed by the retention policy before its record
 * (ADR-160). `deleting` is the job halfway through; `expired` is done. In both the run summary and its numbers remain.
 */
export type TraceState = RunOut["trace_state"];

/** Whether the trace detail may be fetched and shown. Anything else gets the notice, not empty or partial tabs. */
export const traceAvailable = (state: TraceState | undefined): boolean => state === "available";

export const TRACE_NOTICE: Record<
  Exclude<TraceState, "available">,
  { glyph: string; title: string; text: string }
> = {
  expired: {
    glyph: "◇",
    title: "Trace expired",
    text: "The trace of this run was removed by the retention policy; the run summary and its numbers remain. Events, findings, the waterfall, replay and evaluations are no longer available.",
  },
  deleting: {
    glyph: "↻",
    title: "Trace being removed",
    text: "The retention policy is removing the trace of this run. Events, findings, the waterfall, replay and evaluations are no longer shown; the run summary and its numbers remain.",
  },
};
