"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { formatDuration } from "@/lib/format";
import type { DerivedSpan } from "@/lib/spans";
import {
  FIT,
  FLAG_TEXT,
  MAX_INDENT,
  ROOT_ID,
  agentChip,
  agentOrder,
  autoCollapseDepth,
  buildTree,
  colourClass,
  criticalPath,
  laneRows,
  nodeLabel,
  panView,
  scale,
  ticks,
  timeDomain,
  tooltipFields,
  treeRows,
  viewport,
  zoomView,
  type Node,
  type NodeFlags,
  type RunBounds,
  type View,
  type WaterfallRow,
} from "@/lib/waterfall";

const ROW_HEIGHT = 32;
const ZOOM_STEP = 1.5;
const AGENT_COLOURS = 8;

type Mode = "tree" | "lanes";

type Props = {
  spans: readonly DerivedSpan[];
  bounds: RunBounds;
  spanIdOf: (eventId: string) => string | undefined;
  /** The one selection of the run page: an event id. A row stands for the span that holds that event. */
  selectedEventId: string | null;
  firstErrorId: string | null;
  /** When set, other agents' rows are dimmed and the first filtered lane is scrolled to. */
  agentFilter: ReadonlySet<string> | null;
  onSelect: (eventId: string) => void;
  onActivate: (eventId: string) => void;
  onEscape: () => void;
  onJumpToError: () => void;
  /** Events that just arrived on the live stream (tint) and the one a jump landed on (ring). Decoration only. */
  newIds?: ReadonlySet<string>;
  landedId?: string | null;
};

/** The event a click on this row selects: the span's opener, or its only event. */
const eventOf = (node: Node): string | null => node.span?.head.event_id ?? null;

const flagsOf = (node: Node): (keyof NodeFlags)[] =>
  (Object.keys(node.flags) as (keyof NodeFlags)[]).filter((k) => node.flags[k]);

const rowDomId = (key: string) => `wf-${key}`;

/**
 * The waterfall (spec §97): span rows nested under one run root, or grouped in a lane per agent, on a zoomable and
 * pannable time axis. Only the visible rows are in the DOM; a collapsed subtree is one aggregate row. All calculation
 * lives in `lib/waterfall.ts`; this component draws it and routes input. Text comes from `lib/waterfall` helpers that go
 * through `contentText`, never from raw attributes, and React renders it as text.
 */
export function Waterfall({
  spans,
  bounds,
  spanIdOf,
  selectedEventId,
  newIds,
  landedId,
  firstErrorId,
  agentFilter,
  onSelect,
  onActivate,
  onEscape,
  onJumpToError,
}: Props) {
  const [mode, setMode] = useState<Mode>("tree");
  const [view, setView] = useState<View>(FIT);
  const [toggled, setToggled] = useState<ReadonlySet<string>>(new Set());
  const [hover, setHover] = useState<{ id: string; left: number; top: number } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const tree = useMemo(() => buildTree(spans, bounds), [spans, bounds]);
  const critical = useMemo(() => criticalPath(tree), [tree]);
  const domain = useMemo(() => timeDomain(tree), [tree]);
  const colours = useMemo(() => new Map(agentOrder(tree).map((a, i) => [a, i])), [tree]);
  const rows: WaterfallRow[] = useMemo(
    () => (mode === "tree" ? treeRows(tree, toggled, autoCollapseDepth(tree)) : laneRows(tree)),
    [tree, toggled, mode],
  );
  const sc = scale(domain, view.zoom, view.panMs, 100);
  const vp = viewport(domain, view);

  // Only rows that stand for a span can be selected; the root and lane headers are labels.
  const selectable = useMemo(
    () => rows.flatMap((r, i) => (r.kind !== "lane" && r.node.span ? [i] : [])),
    [rows],
  );

  // The row holding the selected event: its span's row, or the aggregate row that swallowed it.
  const selectedIndex = useMemo(() => {
    if (!selectedEventId) return -1;
    let id = spanIdOf(selectedEventId);
    if (!id) return -1;
    const byKey = new Map(rows.map((r, i) => [r.key, i]));
    while (id && !byKey.has(id)) id = tree.nodes.get(id)?.parentId ?? undefined;
    return id ? (byKey.get(id) ?? -1) : -1;
  }, [selectedEventId, spanIdOf, rows, tree]);
  const selectedRow = selectedIndex >= 0 ? rows[selectedIndex] : undefined;

  // eslint-disable-next-line react-hooks/incompatible-library
  const virt = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 10,
    initialRect: { width: 900, height: 600 },
  });

  useEffect(() => {
    if (selectedIndex >= 0) virt.scrollToIndex(selectedIndex, { align: "auto" });
    // virt is stable per instance; only a selection change should scroll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIndex]);

  const focusAgent = agentFilter && agentFilter.size > 0 ? [...agentFilter][0]! : null;
  useEffect(() => {
    if (mode !== "lanes" || !focusAgent) return;
    const i = rows.findIndex((r) => r.kind === "lane" && r.agentId === focusAgent);
    if (i >= 0) virt.scrollToIndex(i, { align: "start" });
    // Re-focus on a new agent or a mode switch, not on every live row change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusAgent, mode]);

  // Ctrl/Cmd + wheel zooms about the pointer, Shift + wheel pans. A plain wheel scrolls the rows, as it should.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey || e.shiftKey)) return;
      e.preventDefault();
      if (e.shiftKey && !(e.ctrlKey || e.metaKey)) {
        setView((v) => panView(domain, v, Math.sign(e.deltaY || e.deltaX) * 0.2));
        return;
      }
      const track = el.querySelector<HTMLElement>(".wf-track");
      const box = track?.getBoundingClientRect();
      const anchor = box && box.width > 0 ? (e.clientX - box.left) / box.width : 0.5;
      const factor = e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
      setView((v) => zoomView(domain, v, factor, Math.min(Math.max(anchor, 0), 1)));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [domain]);

  const toggle = (id: string) =>
    setToggled((t) => {
      const n = new Set(t);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const selectRow = (i: number) => {
    const r = rows[i];
    if (!r || r.kind === "lane") return;
    const id = eventOf(r.node);
    if (id) onSelect(id);
  };
  const goto = (pos: number) => {
    const i = selectable[Math.max(0, Math.min(selectable.length - 1, pos))];
    if (i !== undefined) selectRow(i);
  };
  const move = (delta: number) => {
    const pos = selectable.indexOf(selectedIndex);
    goto(pos < 0 ? (delta > 0 ? 0 : selectable.length - 1) : pos + delta);
  };

  const zoomIn = () => setView((v) => zoomView(domain, v, ZOOM_STEP));
  const zoomOut = () => setView((v) => zoomView(domain, v, 1 / ZOOM_STEP));
  const panLeft = () => setView((v) => panView(domain, v, -0.25));
  const panRight = () => setView((v) => panView(domain, v, 0.25));
  const fit = () => setView(FIT);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const page = Math.max(1, Math.floor((scrollRef.current?.clientHeight ?? 400) / ROW_HEIGHT) - 1);
    switch (e.key) {
      case "j":
      case "ArrowDown":
        move(1);
        break;
      case "k":
      case "ArrowUp":
        move(-1);
        break;
      case "PageDown":
        move(page);
        break;
      case "PageUp":
        move(-page);
        break;
      case "Home":
        goto(0);
        break;
      case "End":
        goto(selectable.length - 1);
        break;
      case "Enter":
      case " ": {
        const id = selectedRow && selectedRow.kind !== "lane" ? eventOf(selectedRow.node) : null;
        if (id) onActivate(id);
        break;
      }
      case "Escape":
        onEscape();
        break;
      case "e":
        onJumpToError();
        break;
      case "+":
      case "=":
        zoomIn();
        break;
      case "-":
      case "_":
        zoomOut();
        break;
      case "[":
        panLeft();
        break;
      case "]":
        panRight();
        break;
      case "0":
        fit();
        break;
      case "c":
        if (selectedRow && selectedRow.kind !== "lane" && mode === "tree")
          toggle(selectedRow.node.id);
        break;
      case "ArrowLeft":
        if (selectedRow?.kind === "span" && selectedRow.hasChildren && mode === "tree")
          toggle(selectedRow.node.id);
        break;
      case "ArrowRight":
        if (selectedRow?.kind === "aggregate") toggle(selectedRow.node.id);
        break;
      default:
        return;
    }
    e.preventDefault();
  };

  const axis = ticks(domain, vp, 6);
  const hoverNode = hover ? tree.nodes.get(hover.id) : undefined;

  if (tree.spanCount === 0) {
    return (
      <div className="state" data-state="empty" data-testid="waterfall-empty">
        <p className="state-title">No spans in this run</p>
        <p className="muted">
          The waterfall draws spans (events that share a span id). Use the timeline to see this
          run&apos;s events.
        </p>
      </div>
    );
  }

  return (
    <div className="waterfall" data-testid="waterfall" data-mode={mode}>
      <div className="wf-toolbar" role="group" aria-label="Waterfall controls">
        <span className="wf-group" role="group" aria-label="Layout">
          <button type="button" aria-pressed={mode === "tree"} onClick={() => setMode("tree")}>
            Tree
          </button>
          <button type="button" aria-pressed={mode === "lanes"} onClick={() => setMode("lanes")}>
            Lanes
          </button>
        </span>
        <span className="wf-group" role="group" aria-label="Zoom and pan">
          <button type="button" onClick={zoomOut} aria-label="Zoom out" disabled={view.zoom <= 1}>
            −
          </button>
          <button type="button" onClick={zoomIn} aria-label="Zoom in">
            +
          </button>
          <button
            type="button"
            onClick={panLeft}
            aria-label="Pan earlier"
            disabled={view.zoom <= 1}
          >
            ←
          </button>
          <button type="button" onClick={panRight} aria-label="Pan later" disabled={view.zoom <= 1}>
            →
          </button>
          <button type="button" onClick={fit} disabled={view.zoom <= 1 && view.panMs === 0}>
            Fit
          </button>
          <span className="muted" data-testid="wf-zoom" aria-live="polite">
            {view.zoom.toFixed(view.zoom < 10 ? 1 : 0)}×
          </span>
        </span>
        <span className="wf-legend muted">
          <span className="wf-swatch wf-swatch-crit" aria-hidden="true" /> Critical path (thick
          edge)
          <span className="wf-swatch wf-swatch-open" aria-hidden="true" /> No end recorded
        </span>
      </div>
      <p className="muted wf-count" aria-live="polite">
        {tree.spanCount.toLocaleString("en-US")} spans
        {mode === "tree" && autoCollapseDepth(tree) !== null
          ? " (large run: deeper levels start collapsed, press c or → to expand)"
          : ""}
      </p>
      <div className="wf-axis" aria-hidden="true">
        <span className="wf-axis-label">Span</span>
        <span className="wf-track wf-axis-track">
          {axis.map((t) => (
            <span key={t.ms} className="wf-tick" style={{ left: `${sc.x(t.ms)}%` }}>
              {t.label}
            </span>
          ))}
        </span>
        <span className="wf-axis-meta">Duration · status</span>
      </div>
      <div
        ref={scrollRef}
        className="wf-scroll"
        role="treegrid"
        tabIndex={0}
        data-zoom={view.zoom}
        data-pan={Math.round(view.panMs)}
        data-rows={rows.length}
        aria-label={`Waterfall, ${rows.length} rows. j and k or arrows move, Enter opens, e jumps to the first error, plus and minus zoom, brackets pan, 0 fits, c collapses.`}
        aria-rowcount={rows.length}
        aria-activedescendant={
          selectedRow && selectedRow.kind !== "lane" ? rowDomId(selectedRow.key) : undefined
        }
        onKeyDown={(e) => {
          scrollRef.current?.setAttribute("data-nav", "keyboard");
          onKeyDown(e);
        }}
        onPointerDown={() => scrollRef.current?.removeAttribute("data-nav")}
      >
        <div style={{ height: virt.getTotalSize(), position: "relative" }}>
          {virt.getVirtualItems().map((v) => {
            const row = rows[v.index];
            if (!row) return null;
            const style: React.CSSProperties = {
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              height: ROW_HEIGHT,
              transform: `translateY(${v.start}px)`,
            };
            if (row.kind === "lane") {
              const idx = colours.get(row.agentId) ?? 0;
              return (
                <div
                  key={row.key}
                  id={rowDomId(row.key)}
                  role="row"
                  aria-level={1}
                  aria-rowindex={v.index + 1}
                  className="wf-row wf-lane"
                  data-agent-colour={idx % AGENT_COLOURS}
                  data-dim={agentFilter && !agentFilter.has(row.agentId) ? "" : undefined}
                  style={style}
                >
                  <div role="gridcell" className="wf-label">
                    <span className="wf-chip" data-agent-colour={idx % AGENT_COLOURS}>
                      {agentChip(row.agentId)}
                    </span>
                    <span className="muted">
                      {row.count} span{row.count === 1 ? "" : "s"}
                      {row.delegatedBy ? ` · delegated by ${agentChip(row.delegatedBy)}` : ""}
                    </span>
                  </div>
                  <div role="gridcell" className="wf-track">
                    <span
                      className="wf-bar wf-lane-bar"
                      data-error={row.hasError || undefined}
                      style={barStyle(sc.x(row.startMs), sc.w(row.endMs - row.startMs))}
                    />
                  </div>
                  <div role="gridcell" className="wf-meta">
                    {formatDuration(row.endMs - row.startMs)}
                    {row.hasError ? " · ✕ error" : ""}
                  </div>
                </div>
              );
            }
            const n = row.node;
            const span = n.span;
            const isAgg = row.kind === "aggregate";
            const label = nodeLabel(n);
            const flags = flagsOf(n);
            const agent = span?.agentId ?? null;
            const idx = agent ? (colours.get(agent) ?? 0) : 0;
            const status = isAgg
              ? row.aggregate.hasError
                ? "error"
                : (span?.status ?? null)
              : (span?.status ?? null);
            const err = isAgg ? row.aggregate.hasError : span?.hasError === true;
            const start = isAgg ? row.aggregate.startMs : n.startMs;
            const end = isAgg ? row.aggregate.endMs : n.endMs;
            const selected = v.index === selectedIndex;
            const durationText = isAgg
              ? formatDuration(end - start)
              : formatDuration(
                  span?.durationMs ?? (n.flags.unfinished ? null : n.endMs - n.startMs),
                );
            const statusText = err ? `✕ ${status ?? "failed"}` : (status ?? "");
            const text = `${label}${agent ? `, agent ${agentChip(agent)}` : ""}${
              isAgg ? `, ${row.aggregate.count} spans collapsed` : ""
            }, ${durationText}${statusText ? `, ${statusText}` : ""}${
              flags.length ? `, ${flags.map((f) => FLAG_TEXT[f]).join(", ")}` : ""
            }${critical.spanIds.has(n.id) ? ", on the critical path" : ""}`;
            return (
              <div
                key={row.key}
                id={rowDomId(row.key)}
                role="row"
                aria-level={row.level}
                aria-rowindex={v.index + 1}
                aria-selected={span ? selected : undefined}
                aria-expanded={
                  isAgg ? false : row.kind === "span" && row.hasChildren ? true : undefined
                }
                aria-label={text}
                aria-describedby={hover?.id === n.id ? "wf-tip" : undefined}
                className="wf-row"
                data-selected={selected || undefined}
                data-error={err || undefined}
                data-first-error={
                  (span && firstErrorId && span.head.event_id === firstErrorId) || undefined
                }
                data-critical={critical.spanIds.has(n.id) || undefined}
                data-aggregate={isAgg || undefined}
                data-root={n.id === ROOT_ID || undefined}
                data-dim={agentFilter && agent && !agentFilter.has(agent) ? "" : undefined}
                data-arrived={(span && newIds?.has(span.head.event_id)) || undefined}
                data-landed={(span && span.head.event_id === landedId) || undefined}
                data-skewed={n.flags.skewed || undefined}
                data-unfinished={n.flags.unfinished || undefined}
                style={style}
                onClick={() => {
                  const id = eventOf(n);
                  if (!id) return;
                  onSelect(id);
                  onActivate(id);
                }}
                onMouseEnter={(e) => {
                  const box = e.currentTarget.getBoundingClientRect();
                  setHover({ id: n.id, left: box.left + 24, top: box.bottom + 4 });
                }}
                onMouseLeave={() => setHover(null)}
              >
                <div role="gridcell" className="wf-label">
                  <span style={{ width: `${row.indent * 0.5}rem` }} className="wf-indent" />
                  {row.kind === "span" && row.hasChildren && mode === "tree" ? (
                    <button
                      type="button"
                      tabIndex={-1}
                      className="wf-twisty"
                      aria-label={`Collapse ${label}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        toggle(n.id);
                      }}
                    >
                      ▾
                    </button>
                  ) : isAgg ? (
                    <button
                      type="button"
                      tabIndex={-1}
                      className="wf-twisty"
                      aria-label={`Expand ${label}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        toggle(n.id);
                      }}
                    >
                      ▸
                    </button>
                  ) : (
                    <span className="wf-twisty" aria-hidden="true" />
                  )}
                  {agent && (
                    <span className="wf-chip" data-agent-colour={idx % AGENT_COLOURS}>
                      {agentChip(agent)}
                      {n.depth > MAX_INDENT ? ` L${n.depth}` : ""}
                    </span>
                  )}
                  <span className="wf-name">{label}</span>
                  {isAgg && <span className="muted"> (+{row.aggregate.count - 1} spans)</span>}
                </div>
                <div role="gridcell" className="wf-track">
                  <span
                    className={`wf-bar ${colourClass(span?.kind ?? null, isAgg && err ? "error" : status)}`}
                    data-aggregate={isAgg || undefined}
                    data-unfinished={n.flags.unfinished || undefined}
                    data-critical={critical.spanIds.has(n.id) || undefined}
                    style={barStyle(sc.x(start), sc.w(end - start))}
                  />
                </div>
                <div role="gridcell" className="wf-meta">
                  <span className="wf-dur">{durationText}</span>
                  {statusText && <span className="wf-status"> · {statusText}</span>}
                  {flags.map((f) => (
                    <span key={f} className="tag" data-flag={f}>
                      {FLAG_TEXT[f]}
                    </span>
                  ))}
                  {critical.spanIds.has(n.id) && <span className="tag tag-crit">critical</span>}
                </div>
              </div>
            );
          })}
        </div>
      </div>
      {hover &&
        hoverNode &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            id="wf-tip"
            role="tooltip"
            className="wf-tip"
            style={{ left: hover.left, top: hover.top }}
          >
            <dl>
              {tooltipFields(hoverNode, domain.startMs).map((f) => (
                <div key={f.label}>
                  <dt>{f.label}</dt>
                  <dd>{f.value}</dd>
                </div>
              ))}
            </dl>
          </div>,
          document.body,
        )}
    </div>
  );
}

/** Position in percent of the track; the minimum width keeps an instant span visible. */
function barStyle(left: number, width: number): React.CSSProperties {
  return { left: `${left}%`, width: `${Math.max(width, 0)}%` };
}
