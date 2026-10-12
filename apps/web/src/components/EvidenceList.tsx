"use client";

import { formatInt, formatOffset } from "@/lib/format";
import type { EvidenceRow } from "@/lib/evidence";

/**
 * The artifacts captured in the run: diffs and command output. A table, because the question is "which, where, how
 * big"; the content stays in the inspector, loaded on request. Every string comes from captured telemetry and is
 * rendered as text.
 */
export function EvidenceList({
  rows,
  t0,
  selectedEventId,
  onOpen,
}: {
  rows: readonly EvidenceRow[];
  t0: number;
  selectedEventId: string | null;
  onOpen: (eventId: string) => void;
}) {
  return (
    <div className="evidence" role="region" aria-label="Captured artifacts" tabIndex={0}>
      {rows.length === 0 ? (
        <p className="state" data-testid="evidence-empty">
          No artifacts were captured in this run.
        </p>
      ) : (
        <table>
          <caption className="sr-only">Evidence</caption>
          <thead>
            <tr>
              <th scope="col">Kind</th>
              <th scope="col">Source</th>
              <th scope="col">What</th>
              <th scope="col" className="num">
                Size
              </th>
              <th scope="col">
                <span className="sr-only">Open</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const at = formatOffset(Date.parse(r.occurredAt) - t0);
              return (
                <tr key={r.key} data-selected={r.eventId === selectedEventId || undefined}>
                  <td>
                    <span className="badge" data-tone="neutral" data-kind={r.kind}>
                      {r.kind}
                    </span>
                  </td>
                  <td className="evidence-source">
                    {r.eventType} <span className="muted">{at}</span>
                  </td>
                  <td className="evidence-what">{r.what ?? <span className="muted">—</span>}</td>
                  <td className="num">
                    {r.bytes != null ? (
                      `${formatInt(r.bytes)} bytes`
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>
                    <button
                      type="button"
                      aria-label={`Open ${r.kind} from ${r.eventType} at ${at}`}
                      onClick={() => onOpen(r.eventId)}
                    >
                      Open
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
