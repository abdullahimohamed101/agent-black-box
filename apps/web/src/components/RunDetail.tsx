"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RunOut } from "@/lib/api/types";
import {
  formatCost,
  formatDuration,
  formatInt,
  formatOffset,
  formatTimestamp,
  num,
} from "@/lib/format";
import { useRun, useRunEvents } from "@/lib/queries";
import { runsPath, type Base } from "@/lib/routes";
import {
  CLASS_LABELS,
  CLASS_ORDER,
  NO_FILTERS,
  buildRows,
  describe,
  filterEvents,
  firstError,
  groupKeys,
  type EventClass,
  type Filters,
} from "@/lib/timeline";
import { EventDrawer } from "./EventDrawer";
import { StatusBadge } from "./StatusBadge";
import { ErrorState, Loading } from "./States";
import { Timeline } from "./Timeline";

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

/** One sentence that answers "what happened?" before the reader looks at a single event. */
export function headline(
  run: RunOut,
  errorText: string | null,
  errorOffsetMs: number | null,
): string {
  const retries = num(run.summary["retry_count"]) ?? 0;
  const r = retries ? ` after ${retries} retr${retries === 1 ? "y" : "ies"}` : "";
  const first = errorText
    ? ` First error at ${formatOffset(errorOffsetMs ?? 0)}: ${errorText}.`
    : "";
  switch (run.status) {
    case "SUCCESS":
      return `Succeeded in ${formatDuration(run.duration_ms)}${retries ? ` with ${retries} retr${retries === 1 ? "y" : "ies"}` : ""}.${first ? ` It recovered from an error. ${first.trim()}` : ""}`;
    case "FAILED":
    case "TIMED_OUT":
    case "BLOCKED":
      return `${run.status === "FAILED" ? "Failed" : run.status === "TIMED_OUT" ? "Timed out" : "Blocked"}${r} in ${formatDuration(run.duration_ms)}.${first}`;
    case "CANCELLED":
      return `Cancelled after ${formatDuration(run.duration_ms)}.${first}`;
    case "WAITING_FOR_APPROVAL":
    case "WAITING":
      return `Waiting${run.status === "WAITING_FOR_APPROVAL" ? " for approval" : ""}. The agent is paused.${first}`;
    default:
      return `In progress.${first}`;
  }
}

export function RunDetail({ runId, base }: { runId: string; base: Base }) {
  const run = useRun(runId);
  const eventCount = num(run.data?.summary["event_count"]) ?? undefined;
  const ev = useRunEvents(runId, run.isSuccess ? eventCount : undefined);
  const { hasNextPage, isFetchingNextPage, fetchNextPage, isError: evError } = ev;

  // Page the whole run in back-to-back; the first page renders as soon as it lands.
  useEffect(() => {
    if (hasNextPage && !isFetchingNextPage && !evError) void fetchNextPage();
  }, [hasNextPage, isFetchingNextPage, evError, fetchNextPage]);

  const events = useMemo(() => ev.data?.pages.flatMap((p) => p.items) ?? [], [ev.data]);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [drawerEventId, setDrawerEventId] = useState<string | null>(null);
  const timelineWrap = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => filterEvents(events, filters), [events, filters]);
  const rows = useMemo(() => buildRows(filtered, collapsed), [filtered, collapsed]);
  const t0 = events[0] ? Date.parse(events[0].occurred_at) : 0;
  const err = useMemo(() => firstError(events), [events]);
  const drawerEvent = drawerEventId ? events.find((e) => e.event_id === drawerEventId) : undefined;

  const focusTimeline = () =>
    timelineWrap.current?.querySelector<HTMLElement>('[role="listbox"]')?.focus();
  const closeDrawer = useCallback(() => {
    setDrawerEventId(null);
    // Return focus to where the reader was.
    requestAnimationFrame(focusTimeline);
  }, []);

  const activate = (key: string) => {
    const row = rows.find((r) => r.key === key);
    if (!row) return;
    if (row.kind === "group") {
      setCollapsed((c) => {
        const n = new Set(c);
        if (n.has(key)) n.delete(key);
        else n.add(key);
        return n;
      });
    } else setDrawerEventId(row.event.event_id);
  };

  const jumpToError = () => {
    if (!err) return;
    // If a filter hides it, clear filters so the jump always lands.
    const visible = filtered.some((e) => e.event_id === err.event_id);
    if (!visible) setFilters(NO_FILTERS);
    setCollapsed(new Set());
    setSelected(err.event_id);
    setDrawerEventId(err.event_id);
  };

  const toggleClass = (c: EventClass) =>
    setFilters((f) => {
      const cur = new Set(f.classes ?? CLASS_ORDER);
      if (cur.has(c)) cur.delete(c);
      else cur.add(c);
      return { ...f, classes: cur.size === CLASS_ORDER.length ? null : cur };
    });

  if (run.isPending) return <Loading label="Loading run" />;
  if (run.isError) return <ErrorState error={run.error} onRetry={() => void run.refetch()} />;
  const r = run.data;
  const s = r.summary;
  const total = eventCount ?? events.length;
  const loadingMore = ev.isPending || hasNextPage || isFetchingNextPage;
  const allGroups = groupKeys(buildRows(filtered, new Set()));
  const errText = err ? describe(err) || err.event_type : null;

  return (
    <div className="run-detail">
      <p className="crumbs">
        <Link href={runsPath(base)}>← Runs</Link>
      </p>
      <header className="run-head">
        <h1>{r.name ?? r.id}</h1>
        <StatusBadge status={r.status} />
      </header>
      <p className="headline" data-testid="headline">
        {headline(r, errText, err ? Date.parse(err.occurred_at) - t0 : null)}
      </p>
      {r.summary_state !== "current" && (
        <p role="status" className="notice">
          {r.summary_state === "processing"
            ? "Summary is updating; figures below may lag the timeline."
            : "Summary failed to update and needs an operator; the timeline is still accurate."}
        </p>
      )}
      <dl className="stats" aria-label="Run summary">
        <Metric label="Duration" value={formatDuration(r.duration_ms ?? num(s["duration_ms"]))} />
        <Metric label="Cost" value={formatCost(num(s["estimated_cost_usd"]))} />
        <Metric
          label="Tokens"
          value={`${formatInt(num(s["input_tokens"]))} in / ${formatInt(num(s["output_tokens"]))} out`}
        />
        <Metric label="Model calls" value={formatInt(num(s["llm_calls"]))} />
        <Metric label="Tool calls" value={formatInt(num(s["tool_calls"]))} />
        <Metric label="Retries" value={formatInt(num(s["retry_count"]))} />
        <Metric label="Errors" value={formatInt(num(s["error_count"]))} />
        <Metric label="Files changed" value={formatInt(num(s["files_modified"]))} />
      </dl>
      <p className="muted meta">
        Agent {r.agent_id ?? "—"} · started {formatTimestamp(r.started_at)} · run {r.id}
        {Array.isArray(s["models"]) && s["models"].length > 0 && (
          <> · {(s["models"] as string[]).join(", ")}</>
        )}
      </p>

      {err && (
        <div className="banner banner-bad" data-testid="first-error">
          <span>
            <strong>✕ First error</strong> at {formatOffset(Date.parse(err.occurred_at) - t0)}:{" "}
            {errText}
          </span>
          <button type="button" onClick={jumpToError}>
            Show it
          </button>
        </div>
      )}

      <section aria-labelledby="tl-h" className="timeline-section">
        <h2 id="tl-h">Timeline</h2>
        <div className="toolbar" role="group" aria-label="Timeline filters">
          <fieldset>
            <legend>Event classes</legend>
            {CLASS_ORDER.map((c) => (
              <label key={c} className="chip">
                <input
                  type="checkbox"
                  checked={filters.classes ? filters.classes.has(c) : true}
                  onChange={() => toggleClass(c)}
                />
                {CLASS_LABELS[c]}
              </label>
            ))}
          </fieldset>
          <label className="chip">
            <input
              type="checkbox"
              checked={filters.errorsOnly}
              onChange={(e) => setFilters((f) => ({ ...f, errorsOnly: e.target.checked }))}
            />
            Errors only
          </label>
          <button
            type="button"
            disabled={allGroups.length === 0}
            onClick={() => setCollapsed(collapsed.size ? new Set() : new Set(allGroups))}
          >
            {collapsed.size ? "Expand groups" : "Collapse groups"}
          </button>
        </div>
        <p className="muted" role="status" aria-live="polite" data-testid="progress">
          {loadingMore
            ? `Loading events: ${formatInt(events.length)} of ${formatInt(total)}…`
            : `${formatInt(filtered.length)} of ${formatInt(events.length)} events shown`}
          {ev.isError && " (some events failed to load)"}
        </p>
        {ev.isError && events.length === 0 ? (
          <ErrorState error={ev.error} onRetry={() => void ev.refetch()} />
        ) : events.length === 0 && !ev.isPending ? (
          <p className="state">This run has no events yet.</p>
        ) : events.length === 0 ? (
          <Loading label="Loading events" />
        ) : rows.length === 0 ? (
          <div className="state" data-state="empty">
            <p className="state-title">No events match these filters</p>
            <button type="button" onClick={() => setFilters(NO_FILTERS)}>
              Clear filters
            </button>
          </div>
        ) : (
          <div className="split" ref={timelineWrap} data-drawer={drawerEvent ? "open" : "closed"}>
            <Timeline
              rows={rows}
              t0={t0}
              selectedKey={selected}
              firstErrorId={err?.event_id ?? null}
              onSelect={setSelected}
              onActivate={activate}
              onEscape={closeDrawer}
              onJumpToError={jumpToError}
            />
            {drawerEvent && <EventDrawer runId={runId} event={drawerEvent} onClose={closeDrawer} />}
          </div>
        )}
      </section>
    </div>
  );
}
