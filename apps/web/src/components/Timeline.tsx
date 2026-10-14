"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef, useState } from "react";
import { formatOffset, formatDuration } from "@/lib/format";
import { isPolicyService, policyEventLabel, PROVENANCE_TEXT } from "@/lib/policy";
import { useTimers } from "@/lib/useTimers";
import { agentChip } from "@/lib/waterfall";
import {
  CLASS_LABELS,
  describe,
  eventClass,
  faultRowIndex,
  isError,
  isRetry,
  type Row,
} from "@/lib/timeline";
import "../app/run-timeline.css";
import "../app/policy.css";

const ROW_HEIGHT = 36;

type Props = {
  rows: readonly Row[];
  t0: number;
  selectedKey: string | null;
  firstErrorId: string | null;
  /** The step the first error happened at, for the fault line's label. */
  faultStep?: number | null;
  /** Show which agent emitted each event (runs with more than one agent). */
  showAgent?: boolean;
  onSelect: (key: string) => void;
  /** Enter / click: open the drawer for an event, toggle a group. */
  onActivate: (key: string) => void;
  onEscape: () => void;
  onJumpToError: () => void;
  /** Events that just arrived on the live stream (tint) and the one a jump landed on (ring). Decoration only. */
  newIds?: ReadonlySet<string>;
  landedId?: string | null;
  /** Why the jump landed: the first error (a red outline that fades) or any other evidence (an accent one). */
  landedTone?: "error" | "evidence";
  /** How many events have arrived live so far. The list follows the live edge while the reader is on it. */
  arrived?: number;
  /** Replay (Phase 12): the event at the playhead. Its row is marked `aria-current`; it does not move the selection. */
  currentId?: string | null;
  /** Replay: Space plays or pauses instead of opening the selected row (Enter still opens it). */
  onSpace?: () => void;
};

const rowId = (key: string) => `tl-${key}`;

/** A success says nothing: its status stays in the DOM for assistive tech but is not drawn (the fault line is the signal). */
const quiet = (status: string | null | undefined) =>
  status == null || status === "" || status === "success";
const StatusText = ({ text, silent }: { text: string; silent: boolean }) =>
  silent ? <span className="sr-only">{text}</span> : <>{text}</>;

/**
 * Virtualized: only the rows in (and near) the viewport are in the DOM, so a 10,000-event run costs the same as a
 * 100-event run. Rows carry aria-posinset/setsize so assistive tech still hears "row 4,512 of 10,000".
 */
export function Timeline({
  rows,
  t0,
  selectedKey,
  firstErrorId,
  faultStep = null,
  showAgent = false,
  onSelect,
  onActivate,
  onEscape,
  onJumpToError,
  newIds,
  landedId,
  landedTone,
  arrived = 0,
  currentId = null,
  onSpace,
}: Props) {
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

  // The live edge: new rows scroll into view only while the reader is already at the bottom. Anyone who has scrolled
  // away keeps the list exactly where it is and gets a "N new events" control instead.
  const { later } = useTimers();
  const atEdge = useRef(true);
  const seenArrived = useRef(arrived);
  const [leftAt, setLeftAt] = useState<number | null>(null);
  const measureEdge = () => {
    const el = scrollRef.current;
    if (el) atEdge.current = el.scrollTop + el.clientHeight >= el.scrollHeight - ROW_HEIGHT * 1.5;
  };
  const onScroll = () => {
    measureEdge();
    if (atEdge.current) setLeftAt(null);
  };
  useEffect(measureEdge, []);
  // A window or inspector resize changes the viewport height without a scroll event, which would leave `atEdge` stale.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measureEdge);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const rowCount = rows.length;
  useEffect(() => {
    const added = arrived - seenArrived.current;
    seenArrived.current = arrived;
    if (added <= 0) return;
    if (atEdge.current) virt.scrollToIndex(rowCount - 1, { align: "end" });
    else later(() => setLeftAt((v) => v ?? arrived - added), 0);
    // virt is stable per instance; only a live arrival moves the list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [arrived]);
  const away = leftAt == null ? 0 : arrived - leftAt;
  const toEdge = () => {
    virt.scrollToIndex(rowCount - 1, { align: "end" });
    setLeftAt(null);
    scrollRef.current?.focus();
  };

  const faultIndex = useMemo(() => faultRowIndex(rows, firstErrorId), [rows, firstErrorId]);
  const selectedIndex = selectedKey == null ? -1 : rows.findIndex((r) => r.key === selectedKey);
  useEffect(() => {
    if (selectedIndex >= 0) virt.scrollToIndex(selectedIndex, { align: "auto" });
    // virt is stable per instance; only a selection change should scroll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIndex]);

  const move = (to: number) => {
    const i = Math.max(0, Math.min(rows.length - 1, to));
    const r = rows[i];
    if (r) onSelect(r.key);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    // Keyboard moves repeat fast: row transitions are switched off until the pointer is used again (see globals.css).
    scrollRef.current?.setAttribute("data-nav", "keyboard");
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const cur = selectedIndex < 0 ? -1 : selectedIndex;
    const page = Math.max(1, Math.floor((scrollRef.current?.clientHeight ?? 400) / ROW_HEIGHT) - 1);
    switch (e.key) {
      case "j":
      case "ArrowDown":
        move(cur + 1);
        break;
      case "k":
      case "ArrowUp":
        move(cur < 0 ? 0 : cur - 1);
        break;
      case "PageDown":
        move(cur + page);
        break;
      case "PageUp":
        move(cur - page);
        break;
      case "Home":
        move(0);
        break;
      case "End":
        move(rows.length - 1);
        break;
      case " ":
        if (onSpace) {
          onSpace();
          break;
        }
        if (selectedKey) onActivate(selectedKey);
        break;
      case "Enter":
        if (selectedKey) onActivate(selectedKey);
        break;
      case "Escape":
        onEscape();
        break;
      case "e":
        onJumpToError();
        break;
      default:
        return;
    }
    e.preventDefault();
  };

  return (
    <>
      <div
        ref={scrollRef}
        className="timeline"
        role="listbox"
        tabIndex={0}
        aria-label={`Run timeline, ${rows.length} rows. Use j and k or arrow keys to move, Enter to open, e to jump to the first error.`}
        aria-activedescendant={selectedKey ? rowId(selectedKey) : undefined}
        onKeyDown={onKeyDown}
        onPointerDown={() => scrollRef.current?.removeAttribute("data-nav")}
        onScroll={onScroll}
      >
        <div style={{ height: virt.getTotalSize(), position: "relative" }}>
          {faultIndex >= 0 && (
            <div
              className="tl-fault"
              aria-hidden="true"
              data-top={faultIndex === 0 || undefined}
              style={{ top: faultIndex * ROW_HEIGHT }}
            >
              <span className="tl-fault-label">
                first error{faultStep == null ? "" : ` · step ${faultStep}`}
              </span>
            </div>
          )}
          {virt.getVirtualItems().map((v) => {
            const row = rows[v.index];
            if (!row) return null;
            const selected = row.key === selectedKey;
            const style: React.CSSProperties = {
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              height: ROW_HEIGHT,
              transform: `translateY(${v.start}px)`,
            };
            if (row.kind === "group") {
              // A collapsed group stands for the events inside it, so it carries the playhead when the playhead is one of
              // them (an expanded group's own rows are marked instead).
              const groupHoldsCurrent =
                currentId != null && row.collapsed && row.eventIds.includes(currentId);
              return (
                <div
                  key={row.key}
                  id={rowId(row.key)}
                  role="option"
                  aria-selected={selected}
                  aria-label={`Group ${row.label}, ${row.count} events, ${row.collapsed ? "collapsed" : "expanded"}${row.hasError ? ", contains an error" : ""}`}
                  aria-posinset={v.index + 1}
                  aria-setsize={rows.length}
                  className="tl-row tl-group"
                  aria-current={groupHoldsCurrent ? "step" : undefined}
                  data-current={groupHoldsCurrent || undefined}
                  data-selected={selected || undefined}
                  data-error={row.hasError || undefined}
                  data-after-fault={(faultIndex >= 0 && v.index > faultIndex) || undefined}
                  style={style}
                  onClick={() => {
                    onSelect(row.key);
                    onActivate(row.key);
                  }}
                >
                  <span className="tl-off">
                    {formatOffset(Date.parse(row.first.occurred_at) - t0)}
                  </span>
                  <span className="tl-class">{row.collapsed ? "▸" : "▾"} Group</span>
                  <span className="tl-sum">
                    {row.label} <span className="muted">({row.count} events)</span>
                  </span>
                  <span className="tl-dur">{formatDuration(row.durationMs)}</span>
                  <span className="tl-status">
                    <StatusText
                      text={row.hasError ? "✕ error" : (row.status ?? "")}
                      silent={!row.hasError && quiet(row.status)}
                    />
                  </span>
                </div>
              );
            }
            const e = row.event;
            const err = isError(e);
            const cls = eventClass(e.event_type);
            const pol = policyEventLabel(e);
            return (
              <div
                key={row.key}
                id={rowId(row.key)}
                role="option"
                aria-selected={selected}
                aria-current={e.event_id === currentId ? "step" : undefined}
                aria-posinset={v.index + 1}
                aria-setsize={rows.length}
                className="tl-row"
                data-selected={selected || undefined}
                data-current={e.event_id === currentId || undefined}
                data-error={err || undefined}
                data-first-error={e.event_id === firstErrorId || undefined}
                data-after-fault={(faultIndex >= 0 && v.index > faultIndex) || undefined}
                data-indented={row.indented || undefined}
                data-policy={pol ? (pol.tone === "wait" ? "wait" : "outcome") : undefined}
                data-retry={isRetry(e) || undefined}
                data-arrived={newIds?.has(e.event_id) || undefined}
                data-landed={(e.event_id === landedId && landedTone) || undefined}
                style={style}
                onClick={() => {
                  onSelect(row.key);
                  onActivate(row.key);
                }}
              >
                <span className="tl-off">{formatOffset(Date.parse(e.occurred_at) - t0)}</span>
                <span className="tl-class" data-class={cls}>
                  {CLASS_LABELS[cls]}
                </span>
                <span className="tl-sum">
                  {e.event_id === firstErrorId && <span className="sr-only">First error </span>}
                  {isRetry(e) && <span className="tag tag-warn">Retry </span>}
                  {showAgent && <span className="tl-agent">{agentChip(e.agent_id)}</span>}
                  <span className="tl-type">{e.event_type}</span>{" "}
                  {pol ? (
                    <span className="tl-desc">
                      <span className="tl-pol" data-tone={pol.tone}>
                        <span className="pol-glyph" aria-hidden="true">
                          {pol.glyph}
                        </span>
                        <span>{pol.word}</span>
                      </span>
                      {isPolicyService(e) && (
                        <span className="tag tl-prov" title={PROVENANCE_TEXT}>
                          <span className="sr-only">{PROVENANCE_TEXT}. </span>
                          <span aria-hidden="true">policy service</span>
                        </span>
                      )}
                      {pol.detail && <span className="tl-pol-why">{pol.detail}</span>}
                    </span>
                  ) : (
                    <span className="tl-desc">{describe(e)}</span>
                  )}
                </span>
                <span className="tl-dur">{formatDuration(e.duration_ms)}</span>
                <span className="tl-status">
                  <StatusText
                    text={err ? `✕ ${e.status ?? "failed"}` : (e.status ?? "")}
                    silent={!err && quiet(e.status)}
                  />
                </span>
              </div>
            );
          })}
        </div>
      </div>
      {away > 0 && (
        <button type="button" className="tl-new" data-testid="new-events" onClick={toEdge}>
          {away} new event{away === 1 ? "" : "s"}
        </button>
      )}
    </>
  );
}
