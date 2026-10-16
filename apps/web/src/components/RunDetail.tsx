"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { isActive, type EventOut } from "@/lib/api/types";
import { formatCost, formatDuration, formatInt, formatTimestamp, num } from "@/lib/format";
import { useLiveEvents } from "@/lib/live";
import { liveStatus } from "@/lib/liveStatus";
import { useEventDetail, useRun, useRunEvents, useRunFindings } from "@/lib/queries";
import { useTimers } from "@/lib/useTimers";
import { traceAvailable } from "@/lib/traceState";
import { headlineKind, headlineModel, replayHeadline } from "@/lib/runHeadline";
import { ageSeconds, pendingApproval } from "@/lib/policy";
import { useApproval } from "@/lib/policyApi";
import {
  STATUS_AS_OF,
  costDisplay,
  filesDisplay,
  replayClockSkewed,
  useReplay,
} from "@/lib/replay";
import { useMedia } from "@/lib/useMedia";
import { evidenceRows } from "@/lib/evidence";
import { agentTreeRows, agentsFromEvents, summaryAgents } from "@/lib/agents";
import { useSpans } from "@/lib/useSpans";
import { runBounds } from "@/lib/waterfall";
import type { Base } from "@/lib/routes";
import {
  CLASS_ORDER,
  NO_FILTERS,
  buildRows,
  filterEvents,
  firstErrorFor,
  groupKeys,
  type EventClass,
  type Filters,
} from "@/lib/timeline";
import { AgentTree } from "./AgentTree";
import { CodingSummary } from "./CodingSummary";
import { FilterMenu } from "./FilterMenu";
import { EvidenceList } from "./EvidenceList";
import { EventDrawer } from "./EventDrawer";
import { InspectorResizer } from "./InspectorResizer";
import { Evaluations } from "./Evaluations";
import { Findings } from "./Findings";
import { ShieldGlyph } from "./SecuritySeverity";
import { SecurityChip, SecurityFindings, useFindingCounts } from "./SecurityFindings";
import { contextSummary, securityLine } from "@/lib/security";
import { ApprovalBanner } from "./ApprovalBanner";
import { RunHeadline } from "./RunHeadline";
import { StatusBadge } from "./StatusBadge";
import { ReplayTransport } from "./ReplayTransport";
import { ErrorState, Loading } from "./States";
import { TraceNotice } from "./TraceNotice";
import { useCan, useWorkspaceOptional } from "./WorkspaceProvider";
import { Timeline } from "./Timeline";
import { ViewSwitch, type RunView } from "./ViewSwitch";
import { Waterfall } from "./Waterfall";
import "../app/run-context.css";
import "../app/run-motion.css";
import "../app/run-replay.css";

function Metric({
  label,
  value,
  tone,
  note,
  hint,
}: {
  label: string;
  value: string;
  tone?: "warn" | "bad";
  /** Why the value is not exact (replay: "3 calls unpriced"); shown under it. */
  note?: string | null;
  /** The same caveat for a figure with no room for a line: a tooltip and text for screen readers. */
  hint?: string | null;
}) {
  return (
    <div className="stat" data-tone={tone}>
      <dt>{label}</dt>
      <dd title={hint ?? undefined}>
        {value}
        {hint && <span className="sr-only"> ({hint})</span>}
        {note && <small>{note}</small>}
      </dd>
    </div>
  );
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
        <span className="live-glyph" aria-hidden="true">
          {connection.glyph}
        </span>{" "}
        {connection.text}
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

/** Below this the rail, an open context column and the inspector leave the pane under ~350 px (KI-126). */
const SQUEEZED = "(min-width: 833px) and (max-width: 1279px)";
/** Below 88 rem the inspector is narrowed to 20 rem while the context column is open, so the column never has to fold. */
const COMPACT = "(max-width: 1407px)";
/** Motion is decoration: only when the person has not asked for less (and jsdom, which has no matchMedia, gets none). */
const MOTION = "(prefers-reduced-motion: no-preference)";
const EXIT_MS = 110; // the inspector's exit animation (run-motion.css) is 100 ms
const INTRO_MS = 340; // the fault line's entrance (run-motion.css) is 280 ms

/**
 * What the folded context column shows: an icon, a vertical label and the counts a reader looks for first. The counts
 * are decoration for sighted users (the button keeps its fixed accessible name; the title spells them out).
 */
function ContextStripBody({
  runId,
  poll,
  agents,
}: {
  runId: string;
  poll: boolean;
  agents: number;
}) {
  const { reliability: findings, security } = useFindingCounts(runId, poll);
  return (
    <span
      className="strip-body"
      title={`Run context: ${security ? `${security} security, ` : ""}${findings} finding${findings === 1 ? "" : "s"}${agents ? `, ${agents} agents` : ""}`}
    >
      <svg className="strip-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <rect x="1.5" y="2.5" width="13" height="11" rx="1.5" fill="none" stroke="currentColor" />
        <path d="M6 2.5v11" stroke="currentColor" />
      </svg>
      <span className="strip-label">Context</span>
      {security > 0 && (
        <span className="strip-count" data-security="true" aria-hidden="true">
          <ShieldGlyph />
          {security}
        </span>
      )}
      {findings > 0 && (
        <span className="strip-count" data-tone="warn" aria-hidden="true">
          {findings}
        </span>
      )}
      {agents > 0 && (
        <span className="strip-count" aria-hidden="true">
          {agents}
        </span>
      )}
    </span>
  );
}

/** "Context · 2 findings": the closed column still says there is something in it. */
function ContextSummary({ runId, poll }: { runId: string; poll: boolean }) {
  const { security, reliability } = useFindingCounts(runId, poll);
  if (security + reliability === 0) return null;
  return (
    <span className="context-summary" data-testid="context-summary">
      {" · "}
      {contextSummary(security, reliability)}
    </span>
  );
}

/**
 * The count that drives the timeline's "follow the live edge / N new events" behaviour. Live arrivals feed it as before;
 * in replay the rows that the playhead adds feed it instead, and live arrivals during replay do not (they are not on the
 * list until the playhead gets there). It never decreases, so switching in and out of replay is not an arrival.
 */
function useFollowCount(replaying: boolean, shownCount: number, arrived: number): number {
  const [c, setC] = useState({ shown: shownCount, arrived, added: 0, ignored: 0 });
  if (c.shown !== shownCount || c.arrived !== arrived) {
    setC({
      shown: shownCount,
      arrived,
      added: replaying && shownCount > c.shown ? c.added + shownCount - c.shown : c.added,
      ignored: replaying && arrived > c.arrived ? c.ignored + arrived - c.arrived : c.ignored,
    });
  }
  return arrived - c.ignored + c.added;
}

const NO_IDS: ReadonlySet<string> = new Set();
const NO_EVENTS: readonly EventOut[] = [];
const MAX_NEW_IDS = 200;

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
  // The trace detail is shown only when the run says it is stored. While retention removes it the events endpoint answers
  // with partial data (KI-198) and afterwards with nothing, so once the run says otherwise nothing more is requested
  // (no paging, polling, stream or context queries) and nothing of it is shown. The first events page is requested
  // alongside the run, as before, so the header does not wait for a second round trip on every run page.
  const available = traceAvailable(run.data?.trace_state);
  const eventCount = num(run.data?.summary["event_count"]) ?? undefined;
  const active = run.data ? isActive(run.data.status) : true;
  const ev = useRunEvents(runId, active, !run.data || available);
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
    if (available && hasNextPage && !isFetchingNextPage && !evError) void fetchNextPage();
  }, [available, hasNextPage, isFetchingNextPage, evError, fetchNextPage, pageCount]);

  const restEvents = useMemo(() => ev.data?.pages.flatMap((p) => p.items) ?? [], [ev.data]);
  const restComplete = ev.isSuccess && !hasNextPage;
  const refetchRun = run.refetch;
  const stream = useLiveEvents(runId, {
    enabled: live && active && available,
    restEvents,
    restComplete,
    reconcile: () => void refetchEvents(),
    onEnd: () => void refetchRun(),
  });
  const events = available ? stream.events : NO_EVENTS;
  const streaming =
    stream.state === "connecting" || stream.state === "live" || stream.state === "reconnecting";

  // Without a working stream (fixtures, or live updates unavailable) an active run falls back to polling:
  // refetch the events when the run grew or changed state, never for a finished run.
  const seen = useRef<string | null>(null);
  const marker = run.data ? `${eventCount ?? ""}|${run.data.status}` : null;
  useEffect(() => {
    if (marker == null || streaming || !available) return;
    if (
      seen.current !== null &&
      seen.current !== marker &&
      (active || seen.current.split("|")[1] !== run.data?.status)
    ) {
      void refetchEvents();
    }
    seen.current = marker;
  }, [marker, active, streaming, available, refetchEvents, run.data?.status]);

  // Timeline or waterfall, kept in the URL (`?view=waterfall`) so a link to the waterfall is shareable.
  const pathname = usePathname();
  const params = useSearchParams();
  const wanted = params?.get("view");
  const paramView: RunView = wanted === "waterfall" || wanted === "evidence" ? wanted : "timeline";
  const [chosen, setChosen] = useState({ view: paramView, param: paramView });
  if (chosen.param !== paramView) setChosen({ view: paramView, param: paramView }); // the URL changed under us
  const view = chosen.view;
  const changeView = (next: RunView) => {
    setChosen((c) => ({ ...c, view: next }));
    // The address bar, not `params`: replay writes `?t=` through history.replaceState and Next's copy can lag a tick.
    const q = new URLSearchParams(window.location.search);
    if (next !== "timeline") q.set("view", next);
    else q.delete("view");
    const qs = q.toString();
    window.history.replaceState(null, "", qs ? `${pathname}?${qs}` : pathname);
  };

  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [drawerEventId, setDrawerEventId] = useState<string | null>(null);
  // The inspector eases out for 100 ms after it is closed: keep rendering the last event until then.
  const [leaving, setLeaving] = useState<EventOut | null>(null);
  const timelineWrap = useRef<HTMLDivElement>(null);
  // Phase 22 D3: the context column (story, agents, findings, evaluations) starts closed; the Context button says what
  // is in it. Once the person opens it, it stays open beside the inspector (the inspector narrows instead, KI-126);
  // only in a window too small for rail + column + inspector + pane (below 1280 px) does it fold while the inspector
  // is open. A click on the strip or the button while it is squeezed "pins" it open until the next toggle.
  const [contextPref, setContextPref] = useState<"closed" | "open" | "pinned">("closed");
  const [inspectorPx, setInspectorPx] = useState<number | null>(null);
  const squeezedWindow = useMedia(SQUEEZED);
  const compactWindow = useMedia(COMPACT);

  // "last event 3 s ago" ticks while the run is live; a finished run's headline has no clock in it.
  const [now, setNow] = useState(() => Date.now());
  // Every active status ticks: a run awaiting approval or queued shows how long it has waited (review P1-2).
  const ticking = !!run.data && isActive(run.data.status);
  useEffect(() => {
    if (!ticking) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [ticking]);

  // A run waiting for a person: the real expiry comes from the approval itself, for roles that may read approvals.
  const canReadApprovals = useCan("approval.read");
  const wsCtx = useWorkspaceOptional();
  const waitingOn = run.data?.status === "WAITING_FOR_APPROVAL" ? pendingApproval(events) : null;
  const approval = useApproval(waitingOn?.id ?? null, canReadApprovals && waitingOn != null);
  const approvalLeft =
    approval.data?.state === "REQUESTED"
      ? ageSeconds(approval.data.seconds_left, approval.dataUpdatedAt, now)
      : null;

  // Earliest timestamp, not events[0]: sequence order can disagree with clocks.
  const t0 = useMemo(
    () => events.reduce((m, e) => Math.min(m, Date.parse(e.occurred_at)), Infinity),
    [events],
  );
  // The server's stored first error when the summary is current (it is known before its page has loaded), else the
  // first error among the loaded events; the headline derives it the same way (`deriveFailure`). `err` is the loaded
  // event, which the waterfall needs for its time.
  const runStatus = run.data?.status ?? "RUNNING";
  const stored = run.data?.summary_state === "current" ? run.data.first_error?.event_id : null;
  const firstErrorId = useMemo(
    () => stored ?? firstErrorFor(runStatus, events)?.event_id ?? null,
    [stored, runStatus, events],
  );
  const err = useMemo(
    () => (firstErrorId ? events.find((e) => e.event_id === firstErrorId) : undefined),
    [firstErrorId, events],
  );
  const fullBounds = useMemo(
    () => runBounds(run.data ?? { started_at: "", completed_at: null, summary: {} }, t0),
    [run.data, t0],
  );

  // ---- Replay (Phase 12): one playhead, one projection. `rs` is the state as of the playhead (null outside replay, or
  // while no event has loaded); every panel below reads it, so nothing keeps its own copy of "the state so far". ----
  // A finished run's exact file count, for the viewer whose per-event paths are withheld (it replaces the per-event count
  // at the last event). Not while the run is live or its summary is catching up: the last loaded event is not the last.
  const filesModified =
    run.data && !active && run.data.summary_state === "current"
      ? num(run.data.summary["files_modified"])
      : null;
  // The inspector counts as open for Esc only while it is on screen: in replay it is hidden while the playhead has not
  // reached its event, and a hidden dialog must not swallow Esc. Read at key time, from the render that follows.
  const inspectorShown = useRef(false);
  const replay = useReplay({
    events,
    ctx: { status: runStatus, firstErrorId, filesModified },
    eventsComplete: restComplete,
    inspectorOpen: () => inspectorShown.current,
  });
  const rs = replay.mode && replay.state && replay.state.total > 0 ? replay.state : null;
  const shown = rs ? rs.events : events;
  const reachedFault = rs ? rs.firstErrorReached : true;
  const skewed = useMemo(
    () => (replay.mode && run.data ? replayClockSkewed(run.data, events) : false),
    [replay.mode, run.data, events],
  );
  // Open spans grow to the playhead along the run's full axis.
  const bounds = useMemo(
    () => (rs ? { ...fullBounds, openEndMs: t0 + rs.elapsedMs } : fullBounds),
    [fullBounds, rs, t0],
  );
  const agentsSoFar = useMemo(
    () => (rs ? new Set(rs.events.map((e) => e.agent_id)).size : null),
    [rs],
  );
  const filtered = useMemo(() => filterEvents(shown, filters), [shown, filters]);
  const rows = useMemo(() => buildRows(filtered, collapsed), [filtered, collapsed]);
  // The playhead's event is the last of the events so far; a filter that removes it leaves no row to mark.
  const playheadHidden = !!rs && !!rs.current && filtered.at(-1)?.event_id !== rs.current.event_id;
  const agents = useMemo(() => {
    const listed = run.data ? summaryAgents(run.data.summary) : null;
    return agentTreeRows(listed ?? agentsFromEvents(events));
  }, [run.data, events]);
  const multiAgent = agents.length > 1;
  const { spans, spanIdOf } = useSpans(shown, view === "waterfall");
  const toggleAgent = (agentId: string) =>
    setFilters((f) => {
      const cur = new Set(f.agents ?? []);
      if (cur.has(agentId)) cur.delete(agentId);
      else cur.add(agentId);
      return { ...f, agents: cur.size === 0 ? null : cur };
    });
  // A filter or collapse can remove the selected row; never leave aria-activedescendant pointing at nothing.
  const selectedKey = selected && rows.some((r) => r.key === selected) ? selected : null;
  // In replay the inspector never shows an event the playhead has not reached, and follows the playhead once playback
  // pauses or steps (not on every tick: each follow is a request for that event's detail).
  const eventIndex = useMemo(() => new Map(events.map((e, i) => [e.event_id, i])), [events]);
  const drawerId =
    rs && drawerEventId != null && (eventIndex.get(drawerEventId) ?? -1) > rs.index
      ? null
      : drawerEventId;
  const currentId = rs?.current?.event_id ?? null;
  const [followed, setFollowed] = useState<string | null>(null);
  if (!rs) {
    if (followed !== null) setFollowed(null);
  } else if (!replay.playing && followed !== currentId) {
    setFollowed(currentId);
    if (drawerEventId != null && currentId != null && drawerEventId !== currentId)
      setDrawerEventId(currentId);
  }
  const loadedDrawerEvent = drawerId ? events.find((e) => e.event_id === drawerId) : undefined;
  // A finding can cite an event the timeline has not paged in yet: fetch just that event (content is withheld
  // per actor by the same route the drawer always uses).
  const remoteDrawer = useEventDetail(runId, drawerId && !loadedDrawerEvent ? drawerId : null);
  const drawerEvent = loadedDrawerEvent ?? remoteDrawer.data;
  const inspected = drawerEvent ?? leaving;
  useEffect(() => {
    inspectorShown.current = inspected != null || drawerId != null;
  });
  const contextClosed =
    contextPref === "closed" || (contextPref === "open" && !!inspected && squeezedWindow);
  const toggleContext = () =>
    setContextPref(contextClosed ? (squeezedWindow && inspected ? "pinned" : "open") : "closed");

  // Expanding the strip hides the very button that was focused; move focus to the column it opened (it is a tab stop)
  // rather than let it fall to <body>.
  const contextRef = useRef<HTMLElement>(null);
  const focusContext = useRef(false);
  useEffect(() => {
    if (!contextClosed && focusContext.current) {
      focusContext.current = false;
      contextRef.current?.focus();
    }
  }, [contextClosed]);

  const evidence = useMemo(() => evidenceRows(events), [events]);

  // Motion cues (decoration only; run-motion.css plays them under `prefers-reduced-motion: no-preference` and nowhere
  // else). Events that arrive after the run has loaded fade in; the target of a jump gets an outline that fades; a
  // failed run draws its fault line once. All three are bounded and pruned by timers.
  const [newIds, setNewIds] = useState<ReadonlySet<string>>(NO_IDS);
  const [arrived, setArrived] = useState(0);
  const followCount = useFollowCount(!!rs, filtered.length, arrived);
  const [landed, setLanded] = useState<{ id: string; tone: "error" | "evidence" } | null>(null);
  const [faultIntro, setFaultIntro] = useState(false);
  const knownIds = useRef<Set<string> | null>(null);
  const filtersRef = useRef(filters);
  useEffect(() => {
    filtersRef.current = filters;
  }, [filters]);
  const introPlayed = useRef(false);
  const { later } = useTimers();
  useEffect(() => {
    if (!restComplete) return;
    if (knownIds.current === null) {
      knownIds.current = new Set(events.map((e) => e.event_id));
      return;
    }
    const known = knownIds.current;
    const fresh: string[] = [];
    const freshEvents: EventOut[] = [];
    for (const e of events) {
      if (!known.has(e.event_id)) {
        known.add(e.event_id);
        fresh.push(e.event_id);
        freshEvents.push(e);
      }
    }
    if (fresh.length === 0) return;
    // From a timer, not the effect body: the fade is a cue after the commit, not state the render depends on.
    const batch = new Set(fresh.slice(-MAX_NEW_IDS));
    later(() => {
      setNewIds(batch);
      // "N new events" counts what the reader can see: a filter hides the rest (review P3-3).
      setArrived((n) => n + filterEvents(freshEvents, filtersRef.current).length);
      later(() => setNewIds(NO_IDS), 400);
    }, 0);
  }, [events, restComplete, later]);
  // The fault line draws itself once, the first time rows and the first error are both on screen; a filter, a view
  // switch or a re-render later never replays it.
  const hasFault = !!firstErrorId && restComplete;
  useEffect(() => {
    if (!hasFault || introPlayed.current) return;
    introPlayed.current = true;
    later(() => {
      setFaultIntro(true);
      later(() => setFaultIntro(false), INTRO_MS);
    }, 0);
  }, [hasFault, later]);
  // Replay: the line draws itself at the moment the playhead reaches the first error (the same cue, still gated by
  // `prefers-reduced-motion: no-preference` in run-motion.css, so reduced motion gets the line instantly).
  const faultReached = !!rs && rs.firstErrorReached;
  const wasReached = useRef(false);
  useEffect(() => {
    if (faultReached && !wasReached.current) {
      later(() => {
        setFaultIntro(true);
        later(() => setFaultIntro(false), INTRO_MS);
      }, 0);
    }
    wasReached.current = faultReached;
  }, [faultReached, later]);
  const flashLanded = (id: string, tone: "error" | "evidence") => {
    setLanded({ id, tone });
    later(() => setLanded(null), 600);
  };
  const focusTimeline = () =>
    timelineWrap.current
      ?.querySelector<HTMLElement>('[role="listbox"], [role="treegrid"], [role="region"]')
      ?.focus();
  const closeDrawer = () => {
    if (drawerEvent && window.matchMedia?.(MOTION).matches) {
      setLeaving(drawerEvent);
      later(() => setLeaving(null), EXIT_MS);
    }
    setDrawerEventId(null);
    // Return focus to where the reader was.
    requestAnimationFrame(focusTimeline);
  };

  // Esc closes the inspector first; with nothing to close it leaves replay (the transport's own Leave button, `r` and Esc
  // all end up in `replay.leave`).
  const onEscape = () => {
    if (inspected || drawerId || !replay.escape()) closeDrawer();
  };
  // Focus: entering replay leaves focus on the Replay button (the transport is the next tab stop). Leaving by the
  // transport's Leave button unmounts the focused control, so focus returns to the Replay button instead of <body>.
  const replayButton = useRef<HTMLButtonElement>(null);
  const wasReplaying = useRef(replay.mode);
  const focusInTransport = useRef(false);
  useEffect(() => {
    if (!replay.mode) return;
    const onIn = (e: FocusEvent) => {
      focusInTransport.current = e.target instanceof Element && !!e.target.closest(".replay");
    };
    document.addEventListener("focusin", onIn);
    return () => document.removeEventListener("focusin", onIn);
  }, [replay.mode]);
  useEffect(() => {
    if (wasReplaying.current && !replay.mode) {
      // Only when focus was inside the transport (now gone): anyone leaving with `r` or Esc from the list keeps their place.
      if (focusInTransport.current) replayButton.current?.focus();
      focusInTransport.current = false;
    }
    wasReplaying.current = replay.mode;
  }, [replay.mode]);

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

  const jumpTo = (eventId: string, tone: "error" | "evidence" = "evidence") => {
    // In replay a jump moves the playhead there: the state so far always includes the event being shown.
    if (replay.mode) replay.seekToId(eventId);
    flashLanded(eventId, tone);
    // If a filter hides a loaded event, clear filters so the jump always lands.
    const loaded = events.some((e) => e.event_id === eventId);
    if (loaded && !filtered.some((e) => e.event_id === eventId)) setFilters(NO_FILTERS);
    setCollapsed(new Set());
    setSelected(eventId);
    setDrawerEventId(eventId);
  };
  const jumpToError = () => {
    // An event on a page that has not loaded yet is fetched by the inspector, so the control never waits for paging.
    if (firstErrorId) jumpTo(firstErrorId, "error");
  };

  // The security chip and a `?finding=` link open the context column on the Security group.
  const focusSecurity = useRef(false);
  const openSecurity = () => {
    if (contextClosed) {
      focusSecurity.current = true;
      setContextPref(squeezedWindow && inspected ? "pinned" : "open");
    } else focusSecurityHeading();
  };
  const focusSecurityHeading = () => {
    const h = document.getElementById("security-h");
    h?.scrollIntoView?.({ block: "start" });
    h?.focus();
  };
  useEffect(() => {
    if (!contextClosed && focusSecurity.current) {
      focusSecurity.current = false;
      focusSecurityHeading();
    }
  });
  // `?finding=<id>` (a row of the workspace Security page): open the column, mark the finding, show its evidence.
  const wantedFinding = params?.get("finding") ?? null;
  const handledFinding = useRef<string | null>(null);
  const deepFindings = useRunFindings(runId, false, available).data;
  const focusId =
    wantedFinding &&
    deepFindings?.items.some((f) => f.id === wantedFinding && f.category === "security")
      ? wantedFinding
      : null;
  const jumpRef = useRef(jumpTo);
  useEffect(() => {
    jumpRef.current = jumpTo;
  });
  useEffect(() => {
    if (!focusId || handledFinding.current === focusId || !deepFindings) return;
    handledFinding.current = focusId;
    const hit = deepFindings.items.find((f) => f.id === focusId)!;
    const target = securityLine(hit).target;
    later(() => {
      setContextPref("pinned");
      if (target) jumpRef.current(target);
    }, 0);
  }, [focusId, deepFindings, later]);

  // `?event=<id>` (a sample of a policy dry run): select that event and open it once the run's events have loaded.
  const wantedEvent = params?.get("event") ?? null;
  const handledEvent = useRef<string | null>(null);
  useEffect(() => {
    if (!wantedEvent || handledEvent.current === wantedEvent || !restComplete) return;
    handledEvent.current = wantedEvent;
    if (events.some((e) => e.event_id === wantedEvent))
      later(() => jumpRef.current(wantedEvent), 0);
  }, [wantedEvent, restComplete, events, later]);

  const toggleClass = (c: EventClass) =>
    setFilters((f) => {
      const cur = new Set(f.classes ?? CLASS_ORDER);
      if (cur.has(c)) cur.delete(c);
      else cur.add(c);
      return { ...f, classes: cur.size === CLASS_ORDER.length ? null : cur };
    });

  if (run.isPending)
    return (
      <div className="run-detail app-page">
        <header className="run-head" aria-busy="true">
          <RunHeadline kind="failed" variant="skeleton" label="Loading run" />
        </header>
      </div>
    );
  if (run.isError) return <ErrorState error={run.error} onRetry={() => void run.refetch()} />;
  const r = run.data;
  const s = r.summary;
  const gone = r.trace_state !== "available" ? r.trace_state : null;
  const total = Math.max(eventCount ?? 0, events.length);
  const nowDoing = active || stream.state !== "off" ? liveStatus(events) : null;
  const loadingMore = ev.isPending || hasNextPage || isFetchingNextPage;
  const allGroups = groupKeys(buildRows(filtered, new Set()));

  // The header's figures: the whole run, or (in replay) the run as of the playhead.
  const retries = rs ? rs.counts.retries : (num(s["retry_count"]) ?? 0);
  const errors = rs ? rs.counts.errors : (num(s["error_count"]) ?? 0);
  const models = Array.isArray(s["models"]) ? (s["models"] as string[]) : [];
  const model = headlineModel({
    run: r,
    events,
    t0,
    eventsComplete: restComplete,
    now,
    approvalLeftSeconds: approvalLeft,
  });
  // In replay the headline is the one as of the playhead (see `replayHeadline`); the live one would give the outcome away.
  const shownModel = rs ? replayHeadline(model, rs) : model;
  const kind = shownModel.kind;
  // The error variant: the run says it failed but its first error cannot be located because the events did not load.
  const headlineFailed =
    !rs && ev.isError && (model.pending || (!model.failure && headlineKind(r, false) === "failed"));
  // A failed run whose first error is not known yet (older run, events paging in): hold the frame, say nothing wrong.
  const headlineLoading =
    !rs && !ev.isError && !restComplete && !model.failure && headlineKind(r, false) === "failed";

  return (
    <div className="run-detail app-page">
      <header className="run-head">
        <div className="run-title">
          <h1>{r.name ?? r.id}</h1>
          {rs ? (
            <span className="replay-status" data-testid="replay-status">
              {rs.statusText} · {STATUS_AS_OF}
            </span>
          ) : (
            <StatusBadge status={r.status} />
          )}
          <SecurityChip badge={r.security_findings} onOpen={openSecurity} />
          <span className="id muted" title={r.id}>
            {r.id.slice(0, 4)}…{r.id.slice(-6)} · {r.agent_id ?? "—"}
            {models.length > 0 && <> · {models.join(", ")}</>} · ordered by{" "}
            {ev.data?.pages[0]?.ordering_mode ?? r.ordering_mode} · started{" "}
            {formatTimestamp(r.started_at)}
          </span>
        </div>
        {gone ? null : headlineFailed ? (
          <RunHeadline kind={kind} variant="error" onRetry={() => void refetchEvents()} />
        ) : headlineLoading ? (
          <RunHeadline kind={kind} variant="skeleton" />
        ) : (
          <RunHeadline kind={kind} variant="ready" model={shownModel} onGo={jumpToError} />
        )}
        {!rs && r.status === "WAITING_FOR_APPROVAL" && (
          <ApprovalBanner
            capability={waitingOn?.capability ?? null}
            approvalId={waitingOn?.id ?? null}
            workspace={wsCtx?.workspace.slug ?? null}
            canOpen={canReadApprovals}
            leftSeconds={approvalLeft}
          />
        )}
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
        <dl className="stats figures" aria-label="Run summary">
          {rs ? (
            <Metric label="Elapsed" value={formatDuration(rs.elapsedMs)} />
          ) : (
            <Metric
              label="Duration"
              value={formatDuration(r.duration_ms ?? num(s["duration_ms"]))}
            />
          )}
          <Metric
            label="Cost"
            value={rs ? costDisplay(rs).text : formatCost(num(s["estimated_cost_usd"]))}
            note={rs ? costDisplay(rs).note : null}
          />
          <Metric
            label="Tokens"
            value={
              rs
                ? `${formatInt(rs.inputTokens)} in / ${formatInt(rs.outputTokens)} out`
                : `${formatInt(num(s["input_tokens"]))} in / ${formatInt(num(s["output_tokens"]))} out`
            }
          />
          <Metric
            label="Model calls"
            value={formatInt(rs ? rs.counts.modelCalls : num(s["llm_calls"]))}
          />
          <Metric
            label="Tool calls"
            value={formatInt(rs ? rs.counts.toolCalls : num(s["tool_calls"]))}
          />
          <Metric
            label="Retries"
            value={formatInt(retries)}
            tone={retries > 0 ? "warn" : undefined}
          />
          <Metric label="Errors" value={formatInt(errors)} tone={errors > 0 ? "bad" : undefined} />
          {multiAgent && <Metric label="Agents" value={formatInt(agentsSoFar ?? agents.length)} />}
          <Metric
            label="Files changed"
            value={rs ? filesDisplay(rs).text : formatInt(num(s["files_modified"]))}
            hint={rs ? filesDisplay(rs).note : null}
          />
        </dl>
      </header>

      {gone ? (
        <TraceNotice state={gone} />
      ) : (
        <>
          <div className="pane-head">
            <ViewSwitch
              view={view}
              onChange={changeView}
              counts={{
                timeline: events.length ? formatInt(events.length) : undefined,
                evidence: evidence.length ? formatInt(evidence.length) : undefined,
              }}
            />
            <div className="pane-actions">
              <button
                type="button"
                className="context-toggle"
                aria-pressed={!contextClosed}
                aria-controls="run-context"
                onClick={toggleContext}
              >
                Context
                <ContextSummary runId={runId} poll={active || r.summary_state === "processing"} />
              </button>
              <button
                ref={replayButton}
                type="button"
                className="context-toggle replay-toggle"
                aria-pressed={replay.mode}
                aria-keyshortcuts="r"
                disabled={events.length === 0 && !replay.mode}
                onClick={replay.toggleMode}
              >
                Replay <kbd aria-hidden="true">r</kbd>
              </button>
            </div>
          </div>

          {replay.mode && <ReplayTransport replay={replay} skewed={skewed} />}

          <div
            className="work"
            data-inspector={inspected ? "open" : "closed"}
            data-context={contextClosed ? "closed" : "open"}
            data-compact={
              (!contextClosed && !!inspected && compactWindow && !inspectorPx) || undefined
            }
            data-fault-intro={faultIntro || undefined}
            style={
              inspectorPx ? ({ "--insp-w": `${inspectorPx}px` } as React.CSSProperties) : undefined
            }
          >
            {/* Scrolls on its own, so it takes a tab stop: a scrollable region with nothing focusable in it is unreachable by keyboard. */}
            <aside
              id="run-context"
              className="context"
              aria-label="Run context"
              tabIndex={contextClosed ? undefined : 0}
              ref={contextRef}
            >
              <button
                type="button"
                className="context-strip"
                onClick={() => {
                  focusContext.current = true;
                  setContextPref(squeezedWindow && inspected ? "pinned" : "open");
                }}
                aria-label="Show run context"
                aria-expanded="false"
                aria-controls="run-context"
                tabIndex={contextClosed ? 0 : -1}
              >
                <ContextStripBody
                  runId={runId}
                  poll={active || r.summary_state === "processing"}
                  agents={multiAgent ? agents.length : 0}
                />
              </button>
              <div className="context-body" inert={contextClosed || undefined}>
                {replay.mode && (
                  <p className="replay-ctx-note muted" data-testid="replay-context-note">
                    Findings and agent totals reflect the whole run.
                  </p>
                )}
                <SecurityFindings
                  runId={runId}
                  projectId={r.project_id}
                  poll={active || r.summary_state === "processing"}
                  focusId={focusId}
                  onShowEvent={jumpTo}
                />
                {/* Findings first: they are why the column is opened (D3). */}
                <Findings
                  runId={runId}
                  runActive={active}
                  summaryProcessing={r.summary_state === "processing"}
                  onShowEvent={jumpTo}
                />

                <CodingSummary
                  events={shown}
                  onOpen={(id) => {
                    setFilters(NO_FILTERS);
                    setSelected(id);
                    setDrawerEventId(id);
                  }}
                />

                {multiAgent && (
                  <AgentTree
                    rows={agents}
                    current={rs?.currentAgent ?? null}
                    active={filters.agents ?? null}
                    onToggle={toggleAgent}
                    onClear={() => setFilters((f) => ({ ...f, agents: null }))}
                  />
                )}

                <Evaluations runId={runId} onShowEvent={jumpTo} />
              </div>
            </aside>

            <section aria-labelledby="tl-h" className="pane">
              <h2 id="tl-h" className="sr-only">
                {view === "waterfall" ? "Waterfall" : view === "evidence" ? "Evidence" : "Timeline"}
              </h2>
              {view === "timeline" && (
                <div className="toolbar tl-toolbar" role="group" aria-label="Timeline filters">
                  <FilterMenu
                    classes={filters.classes}
                    onToggle={toggleClass}
                    onReset={() => setFilters((f) => ({ ...f, classes: null }))}
                  />
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
              {drawerId && !loadedDrawerEvent && (
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
              {rs && view === "evidence" && (
                <p className="notice replay-note" data-testid="replay-evidence-note">
                  Evidence shows the whole run.
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
                <div className="pane-body" key={view} ref={timelineWrap}>
                  {view === "evidence" ? (
                    <EvidenceList
                      rows={evidence}
                      t0={t0}
                      selectedEventId={selected}
                      onOpen={jumpTo}
                    />
                  ) : view === "timeline" ? (
                    <Timeline
                      rows={rows}
                      t0={t0}
                      selectedKey={selectedKey}
                      firstErrorId={reachedFault ? firstErrorId : null}
                      currentId={currentId}
                      onSpace={rs ? replay.togglePlay : undefined}
                      faultStep={model.failure?.step ?? null}
                      showAgent={multiAgent}
                      onSelect={setSelected}
                      onActivate={activate}
                      onEscape={onEscape}
                      onJumpToError={jumpToError}
                      newIds={newIds}
                      arrived={followCount}
                      landedId={landed?.id ?? null}
                      landedTone={landed?.tone}
                    />
                  ) : (
                    <Waterfall
                      spans={spans}
                      bounds={bounds}
                      spanIdOf={spanIdOf}
                      selectedEventId={selected}
                      firstErrorId={reachedFault ? firstErrorId : null}
                      currentEventId={currentId}
                      replaying={!!rs}
                      onSpace={rs ? replay.togglePlay : undefined}
                      faultAtMs={reachedFault && err ? Date.parse(err.occurred_at) : null}
                      faultStep={model.failure?.step ?? null}
                      agentFilter={filters.agents ?? null}
                      onSelect={setSelected}
                      onActivate={setDrawerEventId}
                      onEscape={onEscape}
                      onJumpToError={jumpToError}
                      newIds={newIds}
                      landedId={landed?.id ?? null}
                      landedTone={landed?.tone}
                    />
                  )}
                </div>
              )}
              <div className="pane-foot">
                <p className="muted" role="status" aria-live="polite" data-testid="progress">
                  {loadingMore
                    ? `Loading events: ${formatInt(events.length)} of ${formatInt(total)}…`
                    : view === "evidence"
                      ? `${formatInt(evidence.length)} artifact${evidence.length === 1 ? "" : "s"} in ${formatInt(events.length)} events`
                      : view === "waterfall"
                        ? rs
                          ? `${formatInt(shown.length)} of ${formatInt(events.length)} events in ${formatInt(spans.length)} spans, replay`
                          : `${formatInt(events.length)} events in ${formatInt(spans.length)} spans`
                        : `${formatInt(filtered.length)} of ${formatInt(events.length)} events shown${rs ? ", replay" : ""}${playheadHidden ? ", the event at the playhead is hidden by the filters" : ""}`}
                  {ev.isError && " (some events failed to load)"}
                </p>
                {view !== "evidence" && (
                  <p className="keys muted" aria-hidden="true">
                    <kbd>j</kbd> <kbd>k</kbd> move · <kbd>↵</kbd> open · <kbd>e</kbd> first error ·{" "}
                    <kbd>r</kbd> replay · <kbd>esc</kbd> close
                  </p>
                )}
              </div>
            </section>

            {drawerEvent && <InspectorResizer width={inspectorPx} onChange={setInspectorPx} />}
            {inspected && (
              <EventDrawer
                runId={runId}
                event={inspected}
                onClose={closeDrawer}
                closing={!drawerEvent}
              />
            )}
          </div>
        </>
      )}
    </div>
  );
}
