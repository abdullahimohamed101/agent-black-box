"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { isActive, type RunOut } from "@/lib/api/types";
import {
  formatCost,
  formatDuration,
  formatInt,
  formatOffset,
  formatTimestamp,
  num,
} from "@/lib/format";
import { useLiveEvents } from "@/lib/live";
import { liveStatus } from "@/lib/liveStatus";
import { useEventDetail, useRun, useRunEvents } from "@/lib/queries";
import { agentTreeRows, agentsFromEvents, summaryAgents } from "@/lib/agents";
import { useSpans } from "@/lib/useSpans";
import { runBounds } from "@/lib/waterfall";
import type { Base } from "@/lib/routes";
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
import { AgentTree } from "./AgentTree";
import { CodingSummary } from "./CodingSummary";
import { EventDrawer } from "./EventDrawer";
import { Evaluations } from "./Evaluations";
import { Findings } from "./Findings";
import { StatusBadge } from "./StatusBadge";
import { ErrorState, Loading } from "./States";
import { Timeline } from "./Timeline";
import { ViewSwitch, type RunView } from "./ViewSwitch";
import { Waterfall } from "./Waterfall";

function Metric({ label, value, tone }: { label: string; value: string; tone?: "warn" | "bad" }) {
  return (
    <div className="stat" data-tone={tone}>
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

/**
 * The headline cut into the parts the header lays out: the lead sentence, the "It recovered from an error." clause (a
 * successful run that hit an error) and the first-error sentence that carries the inline "Show it" control. The text is
 * `headline`'s own, so the two can never disagree.
 */
export function headlineParts(
  run: RunOut,
  errorText: string | null,
  errorOffsetMs: number | null,
): { lead: string; recovered: string; error: string | null } {
  const lead = headline(run, null, null);
  if (!errorText) return { lead, recovered: "", error: null };
  const tail = headline(run, errorText, errorOffsetMs).slice(lead.length);
  const at = tail.indexOf("First error at");
  return { lead, recovered: at > 0 ? tail.slice(0, at) : " ", error: tail.slice(Math.max(at, 0)) };
}

/** Live-connection badge. State is spelled out in words and a glyph, never colour alone. */
function LiveBar({
  state,
  status,
}: {
  state: string;
  status: { label: string; tone: string } | null;
}) {
  const connection =
    state === "live"
      ? { glyph: "●", text: "Live" }
      : state === "connecting"
        ? { glyph: "◌", text: "Connecting" }
        : state === "reconnecting"
          ? { glyph: "↻", text: "Reconnecting" }
          : state === "unauthorized"
            ? { glyph: "⏸", text: "Live updates stopped: your access to this run changed" }
            : { glyph: "⏸", text: "Live updates unavailable, refreshing periodically" };
  const partial = state === "reconnecting" || state === "unavailable" || state === "unauthorized";
  return (
    <div
      className="live-bar"
      role="status"
      aria-live="polite"
      data-testid="live-status"
      data-stream={state}
      data-tone={status?.tone ?? "work"}
    >
      <span className="live-conn">
        <span aria-hidden="true">{connection.glyph}</span> {connection.text}
      </span>
      {status && <span className="live-now">{status.label}</span>}
      {partial && (
        <span className="live-partial" data-testid="partial-data">
          Partial data: some recent events may be missing until the connection recovers.
        </span>
      )}
    </div>
  );
}

/** `matchMedia` as external state; false on the server and where the API is missing (jsdom). */
function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (notify) => {
      if (typeof window.matchMedia !== "function") return () => undefined;
      const m = window.matchMedia(query);
      m.addEventListener("change", notify);
      return () => m.removeEventListener("change", notify);
    },
    () => typeof window.matchMedia === "function" && window.matchMedia(query).matches,
    () => false,
  );
}

export function RunDetail({
  runId,
  live = true,
}: {
  runId: string;
  base: Base;
  /** Stream running runs over SSE. Off for fixture data, which has no live source. */
  live?: boolean;
}) {
  const run = useRun(runId);
  const eventCount = num(run.data?.summary["event_count"]) ?? undefined;
  const active = run.data ? isActive(run.data.status) : true;
  const ev = useRunEvents(runId, active);
  const {
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
    isError: evError,
    refetch: refetchEvents,
  } = ev;

  // Page the whole run in back-to-back; the first page renders as soon as it lands.
  // pageCount is a dependency so a page that lands without an observed `isFetchingNextPage` toggle still triggers the next.
  const pageCount = ev.data?.pages.length ?? 0;
  useEffect(() => {
    if (hasNextPage && !isFetchingNextPage && !evError) void fetchNextPage();
  }, [hasNextPage, isFetchingNextPage, evError, fetchNextPage, pageCount]);

  const restEvents = useMemo(() => ev.data?.pages.flatMap((p) => p.items) ?? [], [ev.data]);
  const restComplete = ev.isSuccess && !hasNextPage;
  const refetchRun = run.refetch;
  const stream = useLiveEvents(runId, {
    enabled: live && active && !!run.data,
    restEvents,
    restComplete,
    reconcile: () => void refetchEvents(),
    onEnd: () => void refetchRun(),
  });
  const events = stream.events;
  const streaming =
    stream.state === "connecting" || stream.state === "live" || stream.state === "reconnecting";

  // Without a working stream (fixtures, or live updates unavailable) an active run falls back to polling:
  // refetch the events when the run grew or changed state, never for a finished run.
  const seen = useRef<string | null>(null);
  const marker = run.data ? `${eventCount ?? ""}|${run.data.status}` : null;
  useEffect(() => {
    if (marker == null || streaming) return;
    if (
      seen.current !== null &&
      seen.current !== marker &&
      (active || seen.current.split("|")[1] !== run.data?.status)
    ) {
      void refetchEvents();
    }
    seen.current = marker;
  }, [marker, active, streaming, refetchEvents, run.data?.status]);

  // Timeline or waterfall, kept in the URL (`?view=waterfall`) so a link to the waterfall is shareable.
  const pathname = usePathname();
  const params = useSearchParams();
  const paramView: RunView = params?.get("view") === "waterfall" ? "waterfall" : "timeline";
  const [chosen, setChosen] = useState({ view: paramView, param: paramView });
  if (chosen.param !== paramView) setChosen({ view: paramView, param: paramView }); // the URL changed under us
  const view = chosen.view;
  const changeView = (next: RunView) => {
    setChosen((c) => ({ ...c, view: next }));
    const q = new URLSearchParams(params?.toString() ?? "");
    if (next === "waterfall") q.set("view", "waterfall");
    else q.delete("view");
    const qs = q.toString();
    window.history.replaceState(null, "", qs ? `${pathname}?${qs}` : pathname);
  };

  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [drawerEventId, setDrawerEventId] = useState<string | null>(null);
  const timelineWrap = useRef<HTMLDivElement>(null);
  // The context column (story, agents, findings, evaluations) yields to the inspector on a window narrower than 100rem;
  // the strip button and the header toggle override that and the choice sticks until the next toggle.
  const [contextPref, setContextPref] = useState<"auto" | "open" | "closed">("auto");
  const narrow = useMedia("(max-width: 100rem)");

  const filtered = useMemo(() => filterEvents(events, filters), [events, filters]);
  const rows = useMemo(() => buildRows(filtered, collapsed), [filtered, collapsed]);
  // Earliest timestamp, not events[0]: sequence order can disagree with clocks.
  const t0 = useMemo(
    () => events.reduce((m, e) => Math.min(m, Date.parse(e.occurred_at)), Infinity),
    [events],
  );
  const err = useMemo(() => firstError(events), [events]);
  const bounds = useMemo(
    () => runBounds(run.data ?? { started_at: "", completed_at: null, summary: {} }, t0),
    [run.data, t0],
  );
  const agents = useMemo(() => {
    const listed = run.data ? summaryAgents(run.data.summary) : null;
    return agentTreeRows(listed ?? agentsFromEvents(events));
  }, [run.data, events]);
  const multiAgent = agents.length > 1;
  const { spans, spanIdOf } = useSpans(events, view === "waterfall");
  const toggleAgent = (agentId: string) =>
    setFilters((f) => {
      const cur = new Set(f.agents ?? []);
      if (cur.has(agentId)) cur.delete(agentId);
      else cur.add(agentId);
      return { ...f, agents: cur.size === 0 ? null : cur };
    });
  // A filter or collapse can remove the selected row; never leave aria-activedescendant pointing at nothing.
  const selectedKey = selected && rows.some((r) => r.key === selected) ? selected : null;
  const loadedDrawerEvent = drawerEventId
    ? events.find((e) => e.event_id === drawerEventId)
    : undefined;
  // A finding can cite an event the timeline has not paged in yet: fetch just that event (content is withheld
  // per actor by the same route the drawer always uses).
  const remoteDrawer = useEventDetail(
    runId,
    drawerEventId && !loadedDrawerEvent ? drawerEventId : null,
  );
  const drawerEvent = loadedDrawerEvent ?? remoteDrawer.data;
  const contextClosed =
    contextPref === "closed" || (contextPref === "auto" && !!drawerEvent && narrow);

  const focusTimeline = () =>
    timelineWrap.current
      ?.querySelector<HTMLElement>('[role="listbox"], [role="treegrid"]')
      ?.focus();
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

  const jumpTo = (eventId: string) => {
    // If a filter hides a loaded event, clear filters so the jump always lands.
    const loaded = events.some((e) => e.event_id === eventId);
    if (loaded && !filtered.some((e) => e.event_id === eventId)) setFilters(NO_FILTERS);
    setCollapsed(new Set());
    setSelected(eventId);
    setDrawerEventId(eventId);
  };
  const jumpToError = () => {
    if (err) jumpTo(err.event_id);
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
  const total = Math.max(eventCount ?? 0, events.length);
  const nowDoing = active || stream.state !== "off" ? liveStatus(events) : null;
  const loadingMore = ev.isPending || hasNextPage || isFetchingNextPage;
  const allGroups = groupKeys(buildRows(filtered, new Set()));
  const errText = err ? describe(err) || err.event_type : null;

  const retries = num(s["retry_count"]) ?? 0;
  const errors = num(s["error_count"]) ?? 0;
  const models = Array.isArray(s["models"]) ? (s["models"] as string[]) : [];
  const parts = headlineParts(r, errText, err ? Date.parse(err.occurred_at) - t0 : null);

  return (
    <div className="run-detail app-page">
      <header className="run-head">
        <div className="run-title">
          <h1>{r.name ?? r.id}</h1>
          <StatusBadge status={r.status} />
          {retries > 0 && (
            <span className="tag">
              {retries} retr{retries === 1 ? "y" : "ies"}
            </span>
          )}
          <span className="id muted" title={r.id}>
            {r.id.slice(0, 4)}…{r.id.slice(-6)} · {r.agent_id ?? "—"}
            {models.length > 0 && <> · {models.join(", ")}</>} · ordered by{" "}
            {ev.data?.pages[0]?.ordering_mode ?? r.ordering_mode} · started{" "}
            {formatTimestamp(r.started_at)}
          </span>
        </div>
        <p className="headline" data-testid="headline">
          {parts.lead}
          {parts.error && (
            <>
              {parts.recovered}
              <span data-testid="first-error">
                {parts.error}{" "}
                <button type="button" className="linkish" onClick={jumpToError}>
                  Show it <kbd aria-hidden="true">e</kbd>
                </button>
              </span>
            </>
          )}
        </p>
        {stream.state !== "off" && stream.state !== "ended" && (
          <LiveBar state={stream.state} status={nowDoing} />
        )}
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
          <Metric
            label="Retries"
            value={formatInt(retries)}
            tone={retries > 0 ? "warn" : undefined}
          />
          <Metric label="Errors" value={formatInt(errors)} tone={errors > 0 ? "bad" : undefined} />
          {multiAgent && <Metric label="Agents" value={formatInt(agents.length)} />}
          <Metric label="Files changed" value={formatInt(num(s["files_modified"]))} />
        </dl>
      </header>

      <div className="pane-head">
        <ViewSwitch view={view} onChange={changeView} />
        <button
          type="button"
          className="context-toggle"
          aria-pressed={!contextClosed}
          aria-controls="run-context"
          onClick={() => setContextPref(contextClosed ? "open" : "closed")}
        >
          Context
        </button>
      </div>

      <div
        className="work"
        data-inspector={drawerEvent ? "open" : "closed"}
        data-context={contextClosed ? "closed" : "open"}
      >
        <aside id="run-context" className="context" aria-label="Run context">
          <button
            type="button"
            className="context-strip"
            onClick={() => setContextPref("open")}
            aria-label="Show run context"
            tabIndex={contextClosed ? 0 : -1}
          >
            Context
          </button>
          <div className="context-body" inert={contextClosed || undefined}>
            <CodingSummary
              events={events}
              onOpen={(id) => {
                setFilters(NO_FILTERS);
                setSelected(id);
                setDrawerEventId(id);
              }}
            />

            {multiAgent && (
              <AgentTree
                rows={agents}
                active={filters.agents ?? null}
                onToggle={toggleAgent}
                onClear={() => setFilters((f) => ({ ...f, agents: null }))}
              />
            )}

            <Findings
              runId={runId}
              runActive={active}
              summaryProcessing={r.summary_state === "processing"}
              onShowEvent={jumpTo}
            />

            <Evaluations runId={runId} onShowEvent={jumpTo} />
          </div>
        </aside>

        <section aria-labelledby="tl-h" className="pane">
          <h2 id="tl-h" className="sr-only">
            {view === "waterfall" ? "Waterfall" : "Timeline"}
          </h2>
          {view === "timeline" && (
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
          )}
          {drawerEventId && !loadedDrawerEvent && (
            <p
              role={remoteDrawer.isError ? "alert" : "status"}
              className="notice"
              data-testid="event-notice"
            >
              {remoteDrawer.isError
                ? "That event could not be loaded; it may not exist in this run."
                : "Loading the event…"}{" "}
              <button type="button" onClick={() => setDrawerEventId(null)}>
                Dismiss
              </button>
            </p>
          )}
          {ev.isError && events.length === 0 ? (
            <ErrorState error={ev.error} onRetry={() => void ev.refetch()} />
          ) : events.length === 0 && !ev.isPending ? (
            <p className="state">This run has no events yet.</p>
          ) : events.length === 0 ? (
            <Loading label="Loading events" />
          ) : view === "timeline" && rows.length === 0 ? (
            <div className="state" data-state="empty">
              <p className="state-title">No events match these filters</p>
              <button type="button" onClick={() => setFilters(NO_FILTERS)}>
                Clear filters
              </button>
            </div>
          ) : (
            <div className="pane-body" ref={timelineWrap}>
              {view === "timeline" ? (
                <Timeline
                  rows={rows}
                  t0={t0}
                  selectedKey={selectedKey}
                  firstErrorId={err?.event_id ?? null}
                  showAgent={multiAgent}
                  onSelect={setSelected}
                  onActivate={activate}
                  onEscape={closeDrawer}
                  onJumpToError={jumpToError}
                />
              ) : (
                <Waterfall
                  spans={spans}
                  bounds={bounds}
                  spanIdOf={spanIdOf}
                  selectedEventId={selected}
                  firstErrorId={err?.event_id ?? null}
                  agentFilter={filters.agents ?? null}
                  onSelect={setSelected}
                  onActivate={setDrawerEventId}
                  onEscape={closeDrawer}
                  onJumpToError={jumpToError}
                />
              )}
            </div>
          )}
          <div className="pane-foot">
            <p className="muted" role="status" aria-live="polite" data-testid="progress">
              {loadingMore
                ? `Loading events: ${formatInt(events.length)} of ${formatInt(total)}…`
                : view === "waterfall"
                  ? `${formatInt(events.length)} events in ${formatInt(spans.length)} spans`
                  : `${formatInt(filtered.length)} of ${formatInt(events.length)} events shown`}
              {ev.isError && " (some events failed to load)"}
            </p>
            <p className="keys muted" aria-hidden="true">
              <kbd>j</kbd> <kbd>k</kbd> move · <kbd>↵</kbd> open · <kbd>e</kbd> first error ·{" "}
              <kbd>esc</kbd> close
            </p>
          </div>
        </section>

        {drawerEvent && <EventDrawer runId={runId} event={drawerEvent} onClose={closeDrawer} />}
      </div>
    </div>
  );
}
