"use client";

import { useState } from "react";
import type { FindingOut, RunOut } from "@/lib/api/types";
import {
  type DetectorOut,
  contextSummary,
  securityCountText,
  securityLine,
  securitySeverityMeta,
  splitFindings,
} from "@/lib/security";
import { stateLine } from "@/lib/findings";
import { useRunFindings, useSecurityDetectors } from "@/lib/queries";
import { ErrorState, Loading } from "./States";
import { SecuritySeverityMark, ShieldGlyph } from "./SecuritySeverity";

/** The detectors that are on, in a quiet disclosure that fetches only when opened. Fixed text from the API. */
export function DetectorsOn({ projectId, label }: { projectId: string | null; label?: string }) {
  const [open, setOpen] = useState(false);
  const q = useSecurityDetectors(projectId, open);
  return (
    <details
      className="sec-detectors"
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
    >
      <summary>{label ?? "What was checked"}</summary>
      {q.isPending ? (
        <Loading label="Loading detectors" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : (
        <DetectorList items={q.data} />
      )}
    </details>
  );
}

export function DetectorList({ items }: { items: readonly DetectorOut[] }) {
  return (
    <ul className="sec-detector-list" aria-label="Detectors">
      {items.map((d) => {
        const off = d.muted || d.active === false;
        return (
          <li key={d.id} data-muted={off || undefined}>
            <span>{d.title}</span>
            <span className="sec-detector-state">
              {d.muted ? "muted" : d.active === false ? "needs an allowlist" : "on"}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** "2 security findings": a quiet chip beside the status. A button that opens the Security group. */
export function SecurityChip({
  badge,
  onOpen,
}: {
  badge: RunOut["security_findings"];
  onOpen: () => void;
}) {
  if (!badge || badge.count <= 0) return null;
  const m = securitySeverityMeta(badge.max_severity ?? "info");
  return (
    <button
      type="button"
      className="sec-chip"
      data-testid="security-chip"
      data-tone={m.tone}
      aria-controls="run-context"
      onClick={onOpen}
    >
      <ShieldGlyph filled={m.style === "filled"} />
      {securityCountText(badge.count)} <span className="sr-only">(most severe: {m.label})</span>
    </button>
  );
}

function Line({
  finding,
  focused,
  onShowEvent,
}: {
  finding: FindingOut;
  focused: boolean;
  onShowEvent: (eventId: string) => void;
}) {
  const line = securityLine(finding);
  return (
    <li
      className="sec-item"
      data-testid="security-finding"
      data-severity={line.severity}
      data-finding-id={line.id}
      data-focus={focused || undefined}
      ref={(el) => {
        if (el && focused) el.scrollIntoView?.({ block: "nearest" });
      }}
    >
      <div className="sec-item-head">
        <SecuritySeverityMark severity={line.severity} />
        <h3>{line.title}</h3>
        {line.target ? (
          <button type="button" onClick={() => onShowEvent(line.target!)}>
            Show evidence
          </button>
        ) : (
          <span className="sec-none">No event to show</span>
        )}
      </div>
      {line.rule && (
        <p className="sec-rule">
          Rule: {line.rule}
          {line.count != null && line.count > 1 ? ` (${line.count} matches)` : ""}
        </p>
      )}
    </li>
  );
}

/**
 * The Security group of the context column, above the reliability findings. It shows only the API's fixed strings and
 * counts (title, rule sentence, count); the evidence link opens the event, where the role rules apply (ADR-131).
 */
export function SecurityFindings({
  runId,
  projectId,
  poll,
  focusId,
  onShowEvent,
}: {
  runId: string;
  projectId: string;
  poll: boolean;
  focusId: string | null;
  onShowEvent: (eventId: string) => void;
}) {
  const q = useRunFindings(runId, poll);
  const items = q.data ? splitFindings(q.data.items).security : [];
  return (
    <section aria-labelledby="security-h" className="security-group" data-testid="security-group">
      <h2 id="security-h" tabIndex={-1}>
        Security
      </h2>
      {q.isPending ? (
        <Loading label="Loading security findings" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : items.length > 0 ? (
        <ul className="sec-list" aria-label="Security findings">
          {items.map((f) => (
            <Line key={f.id} finding={f} focused={f.id === focusId} onShowEvent={onShowEvent} />
          ))}
        </ul>
      ) : q.data.state.status === "current" || q.data.state.status === "outdated" ? (
        <div className="sec-clean" data-testid="security-clean">
          <p>Nothing suspicious found.</p>
          <DetectorsOn projectId={projectId} label="Detectors that are on" />
        </div>
      ) : (
        <p className="sec-clean muted" data-testid="security-pending">
          {stateLine(q.data.state, poll).text}
        </p>
      )}
    </section>
  );
}

/** Counts for the Context button and the folded strip: security and reliability findings of the same query. */
export function useFindingCounts(runId: string, poll: boolean) {
  const items = useRunFindings(runId, poll).data?.items ?? [];
  const { security, reliability } = splitFindings(items);
  return { security: security.length, reliability: reliability.length };
}

export { contextSummary };
