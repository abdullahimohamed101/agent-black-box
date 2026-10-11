"use client";

import { useId } from "react";
import type { DimensionOut, FindingOut } from "@/lib/api/types";
import {
  dimensionLabel,
  evidenceTarget,
  healthMeta,
  kindLabel,
  severityMeta,
  sortFindings,
  stateLine,
  summarizeEvidence,
} from "@/lib/findings";
import { useRunFindings } from "@/lib/queries";
import { ErrorState, Loading } from "./States";

const MAX_SHOWN = 200;

function inputsText(inputs: DimensionOut["inputs"]): string {
  return Object.entries(inputs)
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" ? "—" : String(v)}`)
    .join(" · ");
}

function Health({ dimensions, overall }: { dimensions: readonly DimensionOut[]; overall: string }) {
  const o = healthMeta(overall);
  return (
    <div className="health" data-testid="health">
      <p className="health-overall">
        Overall:{" "}
        <span className="badge" data-tone={o.tone} data-status={overall}>
          <span aria-hidden="true">{o.glyph}</span> {o.label}
        </span>{" "}
        <span className="muted">The worst dimension. No score is computed.</span>
      </p>
      <dl className="health-dims" aria-label="Health dimensions">
        {dimensions.map((d) => {
          const m = healthMeta(d.status);
          const inputs = inputsText(d.inputs);
          return (
            <div key={d.name} className="health-dim" data-dimension={d.name} data-status={d.status}>
              <dt>{dimensionLabel(d.name)}</dt>
              <dd>
                <span className="badge" data-tone={m.tone}>
                  <span aria-hidden="true">{m.glyph}</span> {m.label}
                </span>
                <span className="health-rule">{d.rule}</span>
                {inputs && <span className="health-inputs muted">{inputs}</span>}
              </dd>
            </div>
          );
        })}
      </dl>
    </div>
  );
}

function Finding({
  finding,
  onShowEvent,
}: {
  finding: FindingOut;
  onShowEvent: (eventId: string) => void;
}) {
  const sev = severityMeta(finding.severity);
  const kind = kindLabel(finding.kind);
  const ev = summarizeEvidence(finding);
  const target = evidenceTarget(finding);
  const titleId = useId();
  return (
    <li
      className="finding"
      data-testid="finding"
      data-severity={finding.severity}
      data-kind={finding.kind}
      data-type={finding.finding_type}
      aria-labelledby={titleId}
    >
      <div className="finding-head">
        <span className="badge" data-tone={sev.tone}>
          <span aria-hidden="true">{sev.glyph}</span> {sev.label}
        </span>
        <span className="badge" data-tone="neutral" data-kind={finding.kind}>
          {kind}
        </span>
        <h3 id={titleId}>{finding.title}</h3>
      </div>
      <p>{finding.message}</p>
      {ev.chips.length > 0 && (
        <ul className="chips" aria-label="Pattern">
          {ev.chips.map((c, i) => (
            <li key={i} className="chip chip-static">
              {c}
            </li>
          ))}
        </ul>
      )}
      {ev.facts.length > 0 && (
        <dl className="fields finding-facts">
          {ev.facts.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {ev.steps.length > 0 && (
        <div className="finding-steps">
          <p className="muted">{ev.stepsTitle}</p>
          <ol>
            {ev.steps.map((s, i) => (
              <li key={`${s.eventId}-${i}`}>
                <button
                  type="button"
                  className="link-button"
                  onClick={() => onShowEvent(s.eventId)}
                >
                  {s.label}
                </button>
              </li>
            ))}
          </ol>
        </div>
      )}
      <p className="finding-foot muted">
        {finding.detector_version} · confidence {Math.round(finding.confidence * 100)}%
        {ev.thresholds.length > 0 && (
          <> · rule thresholds {ev.thresholds.map(([k, v]) => `${k} ${v}`).join(", ")}</>
        )}
      </p>
      <button
        type="button"
        disabled={target == null}
        onClick={() => target && onShowEvent(target)}
        aria-describedby={titleId}
      >
        Show evidence
      </button>
    </li>
  );
}

/**
 * The run page's findings and health panel. Derived data: it says how fresh the analysis is and never invents a
 * conclusion; `onShowEvent` selects an event in the timeline (RunDetail owns that state).
 */
export function Findings({
  runId,
  runActive,
  summaryProcessing,
  onShowEvent,
}: {
  runId: string;
  runActive: boolean;
  summaryProcessing: boolean;
  onShowEvent: (eventId: string) => void;
}) {
  const q = useRunFindings(runId, runActive || summaryProcessing);
  return (
    <section aria-labelledby="findings-h" className="findings" data-testid="findings">
      <h2 id="findings-h">Findings</h2>
      {q.isPending ? (
        <Loading label="Loading findings" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <FindingsBody data={q.data} runActive={runActive} onShowEvent={onShowEvent} />
      )}
    </section>
  );
}

function FindingsBody({
  data,
  runActive,
  onShowEvent,
}: {
  data: NonNullable<ReturnType<typeof useRunFindings>["data"]>;
  runActive: boolean;
  onShowEvent: (eventId: string) => void;
}) {
  const line = stateLine(data.state, runActive);
  const items = sortFindings(data.items);
  const shown = items.slice(0, MAX_SHOWN);
  return (
    <div data-state={data.state.status}>
      <p
        className="findings-state"
        role="status"
        aria-live="polite"
        data-testid="findings-state"
        data-tone={line.tone}
      >
        {line.text}
        {runActive && " Severity may change until the run ends."}
      </p>
      <Health dimensions={data.health.dimensions} overall={data.health.overall} />
      {shown.length === 0 ? (
        <p className="findings-empty" data-testid="findings-empty">
          No findings
          {data.state.status === "current" ? "." : " yet."}
        </p>
      ) : (
        <ul className="finding-list" aria-label="Findings">
          {shown.map((f) => (
            <Finding key={f.id} finding={f} onShowEvent={onShowEvent} />
          ))}
        </ul>
      )}
      {items.length > shown.length && (
        <p className="muted">
          Showing the first {MAX_SHOWN} of {items.length} findings.
        </p>
      )}
    </div>
  );
}
