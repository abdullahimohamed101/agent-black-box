"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { run as copy } from "@/copy/run";
import { isActive, type EventOut } from "@/lib/api/types";
import {
  formatCost,
  formatDuration,
  formatInt,
  formatRelative,
  formatTimestamp,
  num,
} from "@/lib/format";
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
import { runsPath, type Base } from "@/lib/routes";
import { viewFromParam, type RunView } from "@/lib/runView";
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
import { buildStory, isCodingRun } from "@/lib/coding";
import { AgentTree } from "./AgentTree";
import { CodingSummary } from "./CodingSummary";
import { FilterMenu } from "./FilterMenu";
import { EvidenceList } from "./EvidenceList";
import { EventDrawer } from "./EventDrawer";
import { InspectorResizer } from "./InspectorResizer";
import { Evaluations } from "./Evaluations";
import { Findings } from "./Findings";
import { SecurityChip, SecurityFindings, useFindingCounts } from "./SecurityFindings";
import { securityLine } from "@/lib/security";
import { ApprovalBanner } from "./ApprovalBanner";
import { RunHeadline } from "./RunHeadline";
import { StatusBadge } from "./StatusBadge";
import { ReplayTransport } from "./ReplayTransport";
import { ErrorState, Loading } from "./States";
import { TitleLine } from "./TitleLine";
import { TraceNotice } from "./TraceNotice";
import { useCan, useWorkspaceOptional } from "./WorkspaceProvider";
import { Timeline } from "./Timeline";
import { Tabs, tabIds } from "./Tabs";
import { Waterfall } from "./Waterfall";
import "../app/run-layout.css";
import "../app/run-panels.css";
import "../app/run-motion.css";
import "../app/run-replay.css";

function Metric({
  label,
  value,
  note,
  hint,
  extra,
}: {
  /** Drops out of the line on a phone (it is in Details there): tokens first, then retries (03). */
  extra?: boolean;
  label: string;
  value: string;
  /** Why the value is not exact (replay: "3 calls unpriced"); shown after it. */
  note?: string | null;
  /** The same caveat as a tooltip and text for screen readers. */
  hint?: string | null;
}) {
  return (
    <div className="fig" data-extra={extra || undefined}>
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

/** Motion is decoration: only when the person has not asked for less (and jsdom, which has no matchMedia, gets none). */
const MOTION = "(prefers-reduced-motion: no-preference)";
/** The Steps column is a column from 1100 px; narrower it is one strip above the pane (03). */
const WIDE = "(min-width: 1100px) and (min-height: 576px)";
/** Below 600 px the first-error facts move into Details so the headline and the line stay in the first screen (03). */
const PHONE = "(max-width: 599px)";
const TAB_BASE = "run-tabs";
const EXIT_MS = 110; // the inspector's exit animation (run-motion.css) is 100 ms
const INTRO_MS = 340; // the fault line's entrance (run-motion.css) is 280 ms

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
  base,
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
  // Known not to be stored: the run has loaded and says so. Before that the page behaves as it always did.
  const traceGone = run.data !== undefined && !available;
  const eventCount = num(run.data?.summary["event_count"]) ?? undefined;
  const active = run.data ? isActive(run.data.status) : true;
  const ev = useRunEvents(runId, active, !traceGone);
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
    if (!traceGone && hasNextPage && !isFetchingNextPage && !evError) void fetchNextPage();
  }, [traceGone, hasNextPage, isFetchingNextPage, evError, fetchNextPage, pageCount]);

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
  const events = traceGone ? NO_EVENTS : stream.events;
  const streaming =
    stream.state === "connecting" || stream.state === "live" || stream.state === "reconnecting";

  // Without a working stream (fixtures, or live updates unavailable) an active run falls back to polling:
  // refetch the events when the run grew or changed state, never for a finished run.
  const seen = useRef<string | null>(null);
  const marker = run.data ? `${eventCount ?? ""}|${run.data.status}` : null;
  useEffect(() => {
    if (marker == null || streaming || traceGone) return;
    if (
      seen.current !== null &&
      seen.current !== marker &&
      (active || seen.current.split("|")[1] !== run.data?.status)
    ) {
      void refetchEvents();
    }
    seen.current = marker;
  }, [marker, active, streaming, traceGone, refetchEvents, run.data?.status]);

  // The tab, kept in the URL (`?view=waterfall`) so a link to it is shareable; the timeline is the absence of the parameter.
  const pathname = usePathname();
  const params = useSearchParams();
  const paramView = viewFromParam(params?.get("view"));
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
  const [inspectorPx, setInspectorPx] = useState<number | null>(null);
  const wide = useMedia(WIDE);
  const phone = useMedia(PHONE);
  const [detailsOpen, setDetailsOpen] = useState(false);

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

  const jumpTo = (eventId: string, tone: "error" | "evidence" = "evidence", stay = false) => {
    // "Show evidence" from the Findings or Evaluations tab shows the event in the run: the timeline, with the row marked
    // (a deep link from the workspace Security page stays on Findings and opens the inspector there).
    if (!stay && (view === "findings" || view === "evaluations")) changeView("timeline");
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

  // The security chip and a `?finding=` link open the Findings tab on the Security group.
  const focusSecurity = useRef(false);
  const openSecurity = () => {
    if (view === "findings") focusSecurityHeading();
    else {
      focusSecurity.current = true;
      changeView("findings");
    }
  };
  const focusSecurityHeading = () => {
    const h = document.getElementById("security-h");
    h?.scrollIntoView?.({ block: "start" });
    h?.focus();
  };
  useEffect(() => {
    if (view === "findings" && focusSecurity.current) {
      focusSecurity.current = false;
      focusSecurityHeading();
    }
  });
  // `?finding=<id>` (a row of the workspace Security page): open Findings, mark the finding, show its evidence.
  const wantedFinding = params?.get("finding") ?? null;
  const handledFinding = useRef<string | null>(null);
  const deepFindings = useRunFindings(runId, false, available).data;
  const focusId =
    wantedFinding &&
    deepFindings?.items.some((f) => f.id === wantedFinding && f.category === "security")
      ? wantedFinding
      : null;
  const jumpRef = useRef(jumpTo);
  const changeViewRef = useRef(changeView);
  useEffect(() => {
    jumpRef.current = jumpTo;
    changeViewRef.current = changeView;
  });
  useEffect(() => {
    if (!focusId || handledFinding.current === focusId || !deepFindings) return;
    handledFinding.current = focusId;
    const hit = deepFindings.items.find((f) => f.id === focusId)!;
    const target = securityLine(hit).target;
    later(() => {
      changeViewRef.current("findings");
      if (target) jumpRef.current(target, "evidence", true);
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

  const poll = active || run.data?.summary_state === "processing";
  const findingCounts = useFindingCounts(runId, poll, available);
  const story = useMemo(() => buildStory(shown), [shown]);
  const hasStory = isCodingRun(shown) && story.length > 0;

  if (run.isPending)
    return (
      <div className="run-detail app-page">
        <header className="run-head" aria-busy="true">
          <RunHeadline kind="failed" variant="skeleton" label={copy.loading.run} />
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

  // The figures: the whole run, or (in replay) the run as of the playhead.
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
  const age = formatRelative(r.started_at, now);
  const findingsTotal = findingCounts.security + findingCounts.reliability;
  const failedSteps = story.filter((x) => x.tone === "bad").length;
  const stepsLabel = hasStory
    ? `${copy.steps.strip(story.length)}${failedSteps ? ` (${copy.steps.failedOf(failedSteps)})` : ""}`
    : `Agents · ${agents.length}`;
  const showSteps = hasStory || multiAgent;
  const stepsBody = (
    <>
      {hasStory && (
        <CodingSummary
          events={shown}
          hideHeading={!wide}
          onOpen={(id) => {
            setFilters(NO_FILTERS);
            setSelected(id);
            setDrawerEventId(id);
          }}
        />
      )}
      {multiAgent && (
        <AgentTree
          rows={agents}
          current={rs?.currentAgent ?? null}
          active={filters.agents ?? null}
          onToggle={toggleAgent}
          onClear={() => setFilters((f) => ({ ...f, agents: null }))}
        />
      )}
    </>
  );
  const afterLine = phone && shownModel.detail && !shownModel.pending ? shownModel.detail : null;

  return (
    <div className="run-detail app-page">
      <header className="run-head">
        <TitleLine href={runsPath(base)} title={r.name ?? r.id}>
          <div className="run-status">
            {rs ? (
              <span className="replay-status" data-testid="replay-status">
                {rs.statusText} · {STATUS_AS_OF}
              </span>
            ) : (
              <>
                <StatusBadge status={r.status} />
                <span className="run-age muted" data-testid="run-age">
                  {" · "}
                  {active ? `started ${age}` : age}
                </span>
              </>
            )}
            <SecurityChip badge={r.security_findings} onOpen={openSecurity} />
          </div>
        </TitleLine>
        {gone ? null : headlineFailed ? (
          <RunHeadline kind={kind} variant="error" onRetry={() => void refetchEvents()} />
        ) : headlineLoading ? (
          <RunHeadline kind={kind} variant="skeleton" />
        ) : (
          <RunHeadline
            kind={kind}
            variant="ready"
            model={shownModel}
            detailElsewhere={!!afterLine}
            onGo={jumpToError}
          />
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
        {r.summary_state !== "current" && (
          <p role="status" className="notice">
            {r.summary_state === "processing" ? copy.summaryUpdating : copy.summaryFailed}
          </p>
        )}
        <div className="run-figs">
          <dl className="figures" aria-label={copy.figures.label}>
            {rs ? (
              <Metric label={copy.figures.elapsed} value={formatDuration(rs.elapsedMs)} />
            ) : (
              <Metric
                label={copy.figures.duration}
                value={formatDuration(r.duration_ms ?? num(s["duration_ms"]))}
              />
            )}
            <Metric
              label={copy.figures.cost}
              value={rs ? costDisplay(rs).text : formatCost(num(s["estimated_cost_usd"]))}
              note={rs ? costDisplay(rs).note : null}
            />
            <Metric
              extra
              label={copy.figures.tokens}
              value={
                rs
                  ? `${formatInt(rs.inputTokens)} in / ${formatInt(rs.outputTokens)} out`
                  : `${formatInt(num(s["input_tokens"]))} in / ${formatInt(num(s["output_tokens"]))} out`
              }
            />
            <Metric extra label={copy.figures.retries} value={formatInt(retries)} />
            <Metric label={copy.figures.errors} value={formatInt(errors)} />
          </dl>
          <button
            type="button"
            className="run-details-toggle"
            aria-expanded={detailsOpen}
            aria-controls="run-details"
            onClick={() => setDetailsOpen((o) => !o)}
          >
            {copy.details}
            <span aria-hidden="true"> {detailsOpen ? "▴" : "▾"}</span>
          </button>
        </div>
        <dl
          id="run-details"
          className="run-details"
          hidden={!detailsOpen}
          aria-label={copy.details}
        >
          <div>
            <dt>{copy.detailsList.id}</dt>
            <dd className="mono" title={r.id}>
              {r.id}
            </dd>
          </div>
          <div>
            <dt>{copy.detailsList.agent}</dt>
            <dd>{r.agent_id ?? "—"}</dd>
          </div>
          {models.length > 0 && (
            <div>
              <dt>{copy.detailsList.model}</dt>
              <dd>{models.join(", ")}</dd>
            </div>
          )}
          <div>
            <dt>{copy.detailsList.ordering}</dt>
            <dd>{ev.data?.pages[0]?.ordering_mode ?? r.ordering_mode}</dd>
          </div>
          <div>
            <dt>{copy.detailsList.started}</dt>
            <dd>{formatTimestamp(r.started_at)}</dd>
          </div>
          {phone && (
            <>
              <div className="phone-only">
                <dt>{copy.figures.tokens}</dt>
                <dd>
                  {rs
                    ? `${formatInt(rs.inputTokens)} in / ${formatInt(rs.outputTokens)} out`
                    : `${formatInt(num(s["input_tokens"]))} in / ${formatInt(num(s["output_tokens"]))} out`}
                </dd>
              </div>
              <div className="phone-only">
                <dt>{copy.figures.retries}</dt>
                <dd>{formatInt(retries)}</dd>
              </div>
            </>
          )}
          <div>
            <dt>{copy.detailsList.modelCalls}</dt>
            <dd>{formatInt(rs ? rs.counts.modelCalls : num(s["llm_calls"]))}</dd>
          </div>
          <div>
            <dt>{copy.detailsList.toolCalls}</dt>
            <dd>{formatInt(rs ? rs.counts.toolCalls : num(s["tool_calls"]))}</dd>
          </div>
          <div>
            <dt>{copy.detailsList.files}</dt>
            <dd title={rs ? (filesDisplay(rs).note ?? undefined) : undefined}>
              {rs ? filesDisplay(rs).text : formatInt(num(s["files_modified"]))}
              {rs && filesDisplay(rs).note && (
                <span className="sr-only"> ({filesDisplay(rs).note})</span>
              )}
            </dd>
          </div>
          {multiAgent && (
            <div>
              <dt>{copy.detailsList.agents}</dt>
              <dd>{formatInt(agentsSoFar ?? agents.length)}</dd>
            </div>
          )}
          {afterLine && (
            <div>
              <dt>{copy.detailsList.after}</dt>
              <dd data-testid="headline-detail">
                {afterLine}
                {shownModel.failure?.withheld && " · details withheld for your role"}
              </dd>
            </div>
          )}
        </dl>
        {stream.state !== "off" && stream.state !== "ended" && (
          <LiveBar state={stream.state} status={nowDoing} />
        )}
      </header>

      {gone ? (
        <TraceNotice state={gone} />
      ) : (
        <>
          <div className="pane-head">
            <Tabs
              label={copy.tabs.label}
              idBase={TAB_BASE}
              value={view}
              onChange={changeView}
              items={[
                {
                  id: "timeline",
                  label: copy.tabs.timeline,
                  count: events.length ? formatInt(events.length) : undefined,
                },
                { id: "waterfall", label: copy.tabs.waterfall },
                {
                  id: "evidence",
                  label: copy.tabs.evidence,
                  count: evidence.length ? formatInt(evidence.length) : undefined,
                },
                {
                  id: "findings",
                  label: copy.tabs.findings,
                  count: findingsTotal ? formatInt(findingsTotal) : undefined,
                },
                { id: "evaluations", label: copy.tabs.evaluations },
              ]}
            />
            <div className="pane-tools">
              {view === "timeline" && (
                <div className="tl-tools" role="group" aria-label={copy.toolbar.label}>
                  <FilterMenu
                    classes={filters.classes}
                    onToggle={toggleClass}
                    onReset={() => setFilters((f) => ({ ...f, classes: null }))}
                    groups={{
                      collapsed: collapsed.size > 0,
                      disabled: allGroups.length === 0,
                      onToggle: () => setCollapsed(collapsed.size ? new Set() : new Set(allGroups)),
                    }}
                  />
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={filters.errorsOnly}
                      onChange={(e) => setFilters((f) => ({ ...f, errorsOnly: e.target.checked }))}
                    />
                    {copy.toolbar.errorsOnly}
                  </label>
                </div>
              )}
              <button
                ref={replayButton}
                type="button"
                className="replay-toggle"
                aria-pressed={replay.mode}
                aria-keyshortcuts="r"
                title={copy.replayHint}
                disabled={events.length === 0 && !replay.mode}
                onClick={replay.toggleMode}
              >
                {copy.replay}
              </button>
            </div>
          </div>

          {replay.mode && <ReplayTransport replay={replay} skewed={skewed} />}

          <div
            className="work"
            data-inspector={inspected ? "open" : "closed"}
            data-steps={showSteps ? "open" : "none"}
            data-fault-intro={faultIntro || undefined}
            style={
              inspectorPx ? ({ "--insp-w": `${inspectorPx}px` } as React.CSSProperties) : undefined
            }
          >
            {showSteps && (
              <aside className="steps" aria-label={hasStory ? copy.steps.heading : "Agents"}>
                {wide ? (
                  stepsBody
                ) : (
                  <details className="steps-strip">
                    <summary>{stepsLabel}</summary>
                    {stepsBody}
                  </details>
                )}
              </aside>
            )}

            <section {...tabIds(TAB_BASE, view)} role="tabpanel" className="pane">
              {view === "timeline" || view === "waterfall" || view === "evidence" ? (
                <h2 id="tl-h" className="sr-only">
                  {view === "waterfall"
                    ? copy.tabs.waterfall
                    : view === "evidence"
                      ? copy.tabs.evidence
                      : copy.tabs.timeline}
                </h2>
              ) : null}
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
              {view === "findings" ? (
                <div className="pane-scroll" key={view}>
                  {replay.mode && (
                    <p className="replay-ctx-note muted" data-testid="replay-context-note">
                      Findings and agent totals reflect the whole run.
                    </p>
                  )}
                  <SecurityFindings
                    runId={runId}
                    projectId={r.project_id}
                    poll={poll}
                    focusId={focusId}
                    onShowEvent={jumpTo}
                  />
                  <Findings
                    runId={runId}
                    runActive={active}
                    summaryProcessing={r.summary_state === "processing"}
                    onShowEvent={jumpTo}
                  />
                </div>
              ) : view === "evaluations" ? (
                <div className="pane-scroll" key={view}>
                  <Evaluations runId={runId} onShowEvent={jumpTo} />
                </div>
              ) : ev.isError && events.length === 0 ? (
                <ErrorState error={ev.error} onRetry={() => void ev.refetch()} />
              ) : events.length === 0 && !ev.isPending ? (
                <p className="state">{copy.empty.noEvents}</p>
              ) : events.length === 0 ? (
                <Loading label={copy.loading.events} />
              ) : view === "timeline" && rows.length === 0 ? (
                <div className="state" data-state="empty">
                  <p className="state-title">{copy.empty.noMatch}</p>
                  <button type="button" onClick={() => setFilters(NO_FILTERS)}>
                    {copy.empty.clear}
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
                {(view === "timeline" || view === "waterfall") && (
                  <p className="keys muted" aria-hidden="true">
                    {copy.foot.hints}
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
