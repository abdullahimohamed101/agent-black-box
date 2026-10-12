"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef } from "react";
import { formatInt, formatOffset } from "@/lib/format";
import type { EvidenceRow } from "@/lib/evidence";

const ROW_HEIGHT = 36;

/**
 * The artifacts captured in the run: diffs and command output. A table, because the question is "which, where, how
 * big"; the content stays in the inspector, loaded on request. Every string comes from captured telemetry and is
 * rendered as text. Virtualized like the timeline: a run can hold one row per event (or three), so only the rows near
 * the viewport are in the DOM; aria-hidden spacer rows keep the scroll height, aria-rowcount/-rowindex keep the count.
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
  const scrollRef = useRef<HTMLDivElement>(null);
  // TanStack Virtual returns unmemoizable functions; the React Compiler skips this component, which is acceptable here.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virt = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
    initialRect: { width: 900, height: 600 },
  });
  const selectedIndex =
    selectedEventId == null ? -1 : rows.findIndex((r) => r.eventId === selectedEventId);
  useEffect(() => {
    if (selectedIndex >= 0) virt.scrollToIndex(selectedIndex, { align: "auto" });
    // virt is stable per instance; only a selection change should scroll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIndex]);
  const items = virt.getVirtualItems();
  const padTop = items.length ? items[0]!.start : 0;
  const padBottom = items.length ? virt.getTotalSize() - items[items.length - 1]!.end : 0;

  return (
    <div
      className="evidence"
      role="region"
      aria-label="Captured artifacts"
      tabIndex={0}
      ref={scrollRef}
    >
      {rows.length === 0 ? (
        <p className="state" data-testid="evidence-empty">
          No artifacts were captured in this run.
        </p>
      ) : (
        <table aria-rowcount={rows.length + 1}>
          <caption className="sr-only">Evidence</caption>
          <thead>
            <tr aria-rowindex={1}>
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
            {padTop > 0 && (
              <tr aria-hidden="true" className="evidence-pad">
                <td colSpan={5} style={{ height: padTop }} />
              </tr>
            )}
            {items.map((v) => {
              const r = rows[v.index]!;
              const at = formatOffset(Date.parse(r.occurredAt) - t0);
              return (
                <tr
                  key={r.key}
                  aria-rowindex={v.index + 2}
                  data-selected={r.eventId === selectedEventId || undefined}
                >
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
            {padBottom > 0 && (
              <tr aria-hidden="true" className="evidence-pad">
                <td colSpan={5} style={{ height: padBottom }} />
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
