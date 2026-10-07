"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef } from "react";
import { formatOffset, formatDuration } from "@/lib/format";
import { CLASS_LABELS, describe, eventClass, isError, isRetry, type Row } from "@/lib/timeline";

const ROW_HEIGHT = 36;

type Props = {
  rows: readonly Row[];
  t0: number;
  selectedKey: string | null;
  firstErrorId: string | null;
  onSelect: (key: string) => void;
  /** Enter / click: open the drawer for an event, toggle a group. */
  onActivate: (key: string) => void;
  onEscape: () => void;
  onJumpToError: () => void;
};

const rowId = (key: string) => `tl-${key}`;

/**
 * Virtualized: only the rows in (and near) the viewport are in the DOM, so a 10,000-event run costs the same as a
 * 100-event run. Rows carry aria-posinset/setsize so assistive tech still hears "row 4,512 of 10,000".
 */
export function Timeline({
  rows,
  t0,
  selectedKey,
  firstErrorId,
  onSelect,
  onActivate,
  onEscape,
  onJumpToError,
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
      case "Enter":
      case " ":
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
    <div
      ref={scrollRef}
      className="timeline"
      role="listbox"
      tabIndex={0}
      aria-label={`Run timeline, ${rows.length} rows. Use j and k or arrow keys to move, Enter to open, e to jump to the first error.`}
      aria-activedescendant={selectedKey ? rowId(selectedKey) : undefined}
      onKeyDown={onKeyDown}
    >
      <div style={{ height: virt.getTotalSize(), position: "relative" }}>
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
                data-selected={selected || undefined}
                data-error={row.hasError || undefined}
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
                <span className="tl-status">{row.hasError ? "✕ error" : (row.status ?? "")}</span>
              </div>
            );
          }
          const e = row.event;
          const err = isError(e);
          const cls = eventClass(e.event_type);
          return (
            <div
              key={row.key}
              id={rowId(row.key)}
              role="option"
              aria-selected={selected}
              aria-posinset={v.index + 1}
              aria-setsize={rows.length}
              className="tl-row"
              data-selected={selected || undefined}
              data-error={err || undefined}
              data-first-error={e.event_id === firstErrorId || undefined}
              data-indented={row.indented || undefined}
              data-retry={isRetry(e) || undefined}
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
                <span className="tl-type">{e.event_type}</span>{" "}
                <span className="tl-desc">{describe(e)}</span>
                {e.event_id === firstErrorId && <span className="tag tag-bad"> First error</span>}
                {isRetry(e) && <span className="tag tag-warn"> Retry</span>}
              </span>
              <span className="tl-dur">{formatDuration(e.duration_ms)}</span>
              <span className="tl-status">
                {err ? `✕ ${e.status ?? "failed"}` : (e.status ?? "")}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
