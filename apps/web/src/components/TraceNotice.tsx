import { TRACE_NOTICE, type TraceState } from "@/lib/traceState";
import "../app/trace-notice.css";

/**
 * The one explanation for a run whose trace the retention policy removed (or is removing), used wherever the trace would
 * have been: the run page, in place of the events, findings, waterfall and replay. Text only; never an error and never red.
 */
export function TraceNotice({ state }: { state: Exclude<TraceState, "available"> }) {
  const n = TRACE_NOTICE[state];
  return (
    <div className="trace-notice" role="note" data-testid="trace-notice" data-state={state}>
      <p className="trace-notice-title">
        <span aria-hidden="true">{n.glyph}</span> {n.title}
      </p>
      <p className="muted">{n.text}</p>
    </div>
  );
}

/** The marker in a list: glyph and word, no colour of its own (amber only while the removal is in progress). */
export function TraceMarker({ state }: { state: TraceState }) {
  if (state === "available") return null;
  const n = TRACE_NOTICE[state];
  return (
    <span className="trace-marker" data-testid="trace-marker" data-state={state} title={n.text}>
      <span aria-hidden="true">{n.glyph}</span>{" "}
      {state === "expired" ? "Trace expired" : "Trace being removed"}
    </span>
  );
}
