import type { EventOut } from "@/lib/api/types";
import { num } from "../format";
import { sortEvents, type Orderable } from "../ordering";
import { spanRoleOf } from "../spans";
import { firstErrorFor, isError, isRetry } from "../timeline";
import { isWithheld } from "../withheld";
import { eventTimes } from "./times";

/**
 * Replay projection (Phase 12, D1/D5): the run's state as of a playhead, computed from the events the run page already
 * holds. Pure TypeScript, no React, no DOM, no network. Header figures, the visible rows, the waterfall's open spans and
 * the fault line all read one `ReplayState`, so there is no second copy of "the state so far" to drift.
 *
 * Order is the page's canonical event order (`mergeEvents`/`sortEvents`), never the clock: a skewed multi-agent run has
 * timestamps that go backwards. The playhead is an index into that order, and index `k` means events `0..k` have happened.
 *
 * The projection is incremental: moving forward applies only the new events; a jump restores the nearest checkpoint (a
 * snapshot of the accumulators every `checkpointEvery` events) and re-applies from there. A live run's events only
 * append, and `setEvents` keeps the accumulators and checkpoints across an append (the hook holds one projection per
 * run page), so a new event costs one `apply`, not a re-walk to the playhead.
 *
 * Figures follow the run summary's rules, so at the last event the state equals the header's figures: cost is the cost
 * engine's settled per-call figure the API serves on each event (`cost_usd`, ADR-121); a viewer's withheld file paths
 * are counted per event and flagged approximate (the summary's exact count replaces them at the last event).
 */

export const DEFAULT_CHECKPOINT_EVERY = 256;
export const STATUS_AS_OF = "as of this moment";

/** The run's events in the order replay uses, for a caller holding events in arrival order. */
export const replayOrder = <T extends Orderable>(events: readonly T[]): T[] => sortEvents(events);

export type ReplayCtx = {
  /** The run's status: a BLOCKED run with no error event takes its blocking event as the "first error" (ADR-112). */
  status: string;
  /** The stored first-error event id when the run carries one; otherwise it is found in the events. */
  firstErrorId?: string | null;
  /**
   * The finished run's `files_modified`. A role without `payload.read` has its file paths withheld, so the number of
   * distinct files is unknown until the end; at the last event the exact figure is the summary's. Pass it only when the
   * summary is current and the run is over (otherwise the last loaded event is not the last event).
   */
  filesModified?: number | null;
};

export type ReplaySpan = {
  spanId: string;
  parentSpanId: string | null;
  agentId: string;
  kind: string | null;
  /** Indices into the canonical order: where the span opened and (finished spans) where it closed. */
  startIndex: number;
  endIndex: number | null;
  /** The closer was a failure. */
  failed: boolean;
};

export type ReplayCounts = {
  events: number;
  errors: number;
  retries: number;
  modelCalls: number;
  toolCalls: number;
  filesChanged: number;
};

export type ReplayState = {
  /** The playhead: index of the current event, -1 when the run has no events. */
  index: number;
  total: number;
  /** The event at the playhead. */
  current: EventOut | null;
  /** Events so far, in canonical order (the playhead's event included). */
  events: readonly EventOut[];
  counts: ReplayCounts;
  /**
   * Some file event had a withheld path, so `filesChanged` counts such events individually: an upper bound on the
   * distinct files (a file changed twice counts twice), exact again at the end of a finished run.
   */
  filesApproximate: boolean;
  /**
   * What the completed model calls cost so far, rounded like the summary's: the server's settled figure per call
   * (provider-reported, else price book, else the caller's estimate; ADR-040/121). Unpriced calls add nothing.
   */
  costUsd: number;
  /** Completed model calls nothing could price: the cost above is a floor, not a total. */
  unpricedCalls: number;
  /**
   * Completed model calls the server has not priced yet (a live arrival, a run still being summarised) that were added
   * from the event's own attributes (`cost.provider_usd`, then `cost.estimated_usd`): the cost above may still change.
   */
  unsettledCalls: number;
  inputTokens: number;
  outputTokens: number;
  /** Recorded time from the run's earliest event to the playhead (never decreases, whatever the clocks say). */
  elapsedMs: number;
  /** Index of the first error (or, for a blocked run, the blocking event) in the whole run; -1 when there is none. */
  firstErrorIndex: number;
  firstErrorReached: boolean;
  /** "Running, 1 error so far": say `STATUS_AS_OF` beside it. */
  statusText: string;
  currentAgent: string | null;
  /** Spans opened and not yet closed at the playhead (the waterfall's growing bars). */
  openSpans: readonly ReplaySpan[];
  /** Spans closed by the playhead, in the order they closed. */
  finishedSpans: readonly ReplaySpan[];
};

type Acc = {
  /** How many events have been applied; the playhead is `applied - 1`. */
  applied: number;
  errors: number;
  retries: number;
  modelCalls: number;
  toolCalls: number;
  files: Set<string>;
  filesApprox: boolean;
  cost: number;
  unpriced: number;
  unsettled: number;
  inTok: number;
  outTok: number;
  maxOffset: number;
  pendingApprovals: number;
  /** A `policy.action.blocked` event has been applied. */
  policyBlocked: boolean;
  lastRunType: string | null;
  lastRunStatus: string | null;
  open: Map<string, ReplaySpan>;
  finishedLen: number;
};

const blank = (): Acc => ({
  applied: 0,
  errors: 0,
  retries: 0,
  modelCalls: 0,
  toolCalls: 0,
  files: new Set(),
  filesApprox: false,
  cost: 0,
  unpriced: 0,
  unsettled: 0,
  inTok: 0,
  outTok: 0,
  maxOffset: 0,
  pendingApprovals: 0,
  policyBlocked: false,
  lastRunType: null,
  lastRunStatus: null,
  open: new Map(),
  finishedLen: 0,
});

/** Span objects are replaced, never mutated, so a snapshot shares them. */
const clone = (a: Acc): Acc => ({ ...a, files: new Set(a.files), open: new Map(a.open) });

const MODEL_CALL = /^llm\.request\.(completed|failed)$/;
const TOOL_CALL = /^tool\.call\.(completed|failed)$/;
const FILE_CHANGE = new Set(["file.created", "file.modified", "file.deleted"]);

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

function statusText(a: Acc, runStatus: string): string {
  let base: string;
  switch (a.lastRunType) {
    case "run.completed":
      base =
        a.lastRunStatus === "error"
          ? "Failed"
          : a.lastRunStatus === "timeout"
            ? "Timed out"
            : a.lastRunStatus === "blocked"
              ? "Blocked"
              : "Completed";
      break;
    case "run.failed":
      base = "Failed";
      break;
    case "run.cancelled":
      base = "Cancelled";
      break;
    default:
      // A run that ends BLOCKED is blocked from its blocking event on, not "Running" until its closing event. A policy
      // block in a run that carries on (status is not BLOCKED) stays what it was.
      base =
        a.policyBlocked && runStatus === "BLOCKED"
          ? "Blocked"
          : a.pendingApprovals > 0
            ? "Waiting for approval"
            : "Running";
  }
  return a.errors > 0 ? `${base}, ${plural(a.errors, "error", "errors")} so far` : base;
}

export class ReplayProjection {
  private events: readonly EventOut[] = [];
  private times: Float64Array = new Float64Array(0);
  private t0 = 0;
  private maxOffsetMs = 0;
  private ctx: ReplayCtx;
  /** Bumped when the event list or context changes, so a caller memoising on the projection's state can tell. */
  version = 0;
  private readonly every: number;
  private acc: Acc = blank();
  /** Snapshots by applied-count (multiples of `every`); contiguous from 0 up to the furthest point reached. */
  private checkpoints = new Map<number, Acc>();
  /**
   * Every span that has closed, in event order, discovered once and never truncated: closing is deterministic, so a
   * re-applied event closes the same span. A state's finished spans are the first `finishedLen` of this log.
   */
  private log: ReplaySpan[] = [];
  private logged = 0;
  private firstErr = -1;
  /** Events applied by the last `advance` (tests assert a forward step applies only the new ones). */
  lastApplied = 0;

  constructor(
    events: readonly EventOut[],
    ctx: ReplayCtx,
    opts: { checkpointEvery?: number } = {},
  ) {
    this.every = ReplayProjection.interval(opts.checkpointEvery);
    this.ctx = ctx;
    this.load(events);
  }

  /** The checkpoint interval an option resolves to. */
  static interval(requested?: number): number {
    return Math.max(1, Math.floor(requested ?? DEFAULT_CHECKPOINT_EVERY));
  }

  get checkpointEvery(): number {
    return this.every;
  }

  private load(events: readonly EventOut[]) {
    this.events = events;
    this.times = eventTimes(events);
    let t0 = Infinity;
    for (const t of this.times) if (t < t0) t0 = t;
    this.t0 = Number.isFinite(t0) ? t0 : 0;
    let max = 0;
    for (const t of this.times) if (Number.isFinite(t) && t - this.t0 > max) max = t - this.t0;
    this.maxOffsetMs = max;
    this.firstErr = this.locateFirstError();
    this.version++;
  }

  private locateFirstError(): number {
    const id = this.ctx.firstErrorId ?? firstErrorFor(this.ctx.status, this.events)?.event_id;
    return id ? this.events.findIndex((e) => e.event_id === id) : -1;
  }

  get total(): number {
    return this.events.length;
  }

  /** Index of the run's first error (or blocking event), -1 when there is none; known before the playhead gets there. */
  get firstErrorIndex(): number {
    return this.firstErr;
  }

  /** Recorded time from the earliest event to the latest, whatever order they are in (the scrub bar's full length). */
  get durationMs(): number {
    return this.maxOffsetMs;
  }

  /**
   * Take a new event list (a live run's events grew, or the context changed). When the new list only extends the old
   * one the accumulators and checkpoints stay valid; any other change (a late event landing mid-run, a different run)
   * starts over. Returns whether the old state was kept.
   */
  setEvents(events: readonly EventOut[], ctx: ReplayCtx = this.ctx): boolean {
    const old = this.events;
    // A full prefix check: a late event landing mid-run keeps both ends but moves everything after it.
    let extends_ = events.length >= old.length;
    for (let i = 0; extends_ && i < old.length; i++)
      extends_ = events[i]!.event_id === old[i]!.event_id;
    this.ctx = ctx;
    if (events === old) {
      this.firstErr = this.locateFirstError();
      this.version++;
      return true;
    }
    if (!extends_) {
      this.acc = blank();
      this.checkpoints.clear();
      this.log = [];
      this.logged = 0;
    }
    this.load(events);
    return extends_;
  }

  private apply(i: number) {
    const e = this.events[i]!;
    const a = this.acc;
    const type = e.event_type;
    const attrs = e.attributes;
    if (isError(e)) a.errors++;
    if (isRetry(e)) a.retries++;
    if (MODEL_CALL.test(type)) a.modelCalls++;
    if (TOOL_CALL.test(type)) a.toolCalls++;
    if (FILE_CHANGE.has(type)) {
      const path = attrs["file.path"];
      // The API replaces a withheld path by a marker string and lists the key in `withheld_attributes`: that list is the
      // contract, so every such event counts on its own (the marker would otherwise be one "file" for all of them).
      if (typeof path === "string" && !isWithheld(e, "file.path")) a.files.add(path);
      else {
        a.files.add(`\u0000${e.event_id}`);
        a.filesApprox = true;
      }
    }
    if (type === "llm.request.completed") {
      if (typeof e.cost_usd === "number") {
        // priced by the server: the engine's effective total for this call
        a.cost += e.cost_usd;
        if (e.cost_unpriced) a.unpriced++;
      } else {
        // no stored line yet: the engine's own client-side order (ADR-040), shown as approximate
        const own = num(attrs["cost.provider_usd"]) ?? num(attrs["cost.estimated_usd"]);
        if (own != null) {
          a.cost += own;
          a.unsettled++;
        } else a.unpriced++;
      }
      a.inTok += num(attrs["llm.input_tokens"]) ?? 0;
      a.outTok += num(attrs["llm.output_tokens"]) ?? 0;
    }
    const t = this.times[i]!;
    if (Number.isFinite(t)) a.maxOffset = Math.max(a.maxOffset, t - this.t0);
    if (type === "policy.action.blocked") a.policyBlocked = true;
    if (type === "approval.requested") a.pendingApprovals++;
    else if (type === "approval.granted" || type === "approval.denied")
      a.pendingApprovals = Math.max(0, a.pendingApprovals - 1);
    if (type.startsWith("run.")) {
      a.lastRunType = type;
      a.lastRunStatus = e.status ?? null;
    }
    if (e.span_id) {
      const { role, kind } = spanRoleOf(type);
      if (role === "open" && !a.open.has(e.span_id)) {
        a.open.set(e.span_id, {
          spanId: e.span_id,
          parentSpanId: e.parent_span_id ?? null,
          agentId: e.agent_id,
          kind,
          startIndex: i,
          endIndex: null,
          failed: false,
        });
      } else if (role === "close") {
        const span = a.open.get(e.span_id);
        if (span) {
          a.open.delete(e.span_id);
          a.finishedLen++;
          if (i >= this.logged) this.log.push({ ...span, endIndex: i, failed: isError(e) });
        }
      }
    }
    a.applied = i + 1;
    if (i >= this.logged) this.logged = i + 1;
  }

  /** Move the playhead to `to` (clamped into the run) and return the state there. */
  advance(to: number): ReplayState {
    const n = this.events.length;
    const target =
      n === 0 ? 0 : Math.min(n, Math.max(0, Math.floor(Number.isFinite(to) ? to : 0)) + 1);
    this.lastApplied = 0;
    if (this.acc.applied !== target) {
      // The best starting point: the current state when it is behind the target, or a closer checkpoint.
      let base = this.acc.applied <= target ? this.acc.applied : -1;
      let from: Acc | null = null;
      for (
        let c = Math.floor(target / this.every) * this.every;
        c > base && c > 0;
        c -= this.every
      ) {
        const snap = this.checkpoints.get(c);
        if (snap) {
          from = snap;
          base = c;
          break;
        }
      }
      if (from) this.acc = clone(from);
      else if (base < 0) this.acc = blank();
      for (let i = this.acc.applied; i < target; i++) {
        this.apply(i);
        this.lastApplied++;
        const done = i + 1;
        if (done % this.every === 0 && !this.checkpoints.has(done))
          this.checkpoints.set(done, clone(this.acc));
      }
    }
    return this.state();
  }

  /** The state at the current playhead without moving it. */
  state(): ReplayState {
    const a = this.acc;
    const index = a.applied - 1;
    const so = this.events.slice(0, a.applied);
    // A viewer's per-event file count is replaced by the summary's exact one once the whole finished run has played.
    const exactFiles =
      a.filesApprox && a.applied === this.events.length && this.ctx.filesModified != null
        ? this.ctx.filesModified
        : null;
    return {
      index: this.events.length === 0 ? -1 : index,
      total: this.events.length,
      current: so[index] ?? null,
      events: so,
      counts: {
        events: a.applied,
        errors: a.errors,
        retries: a.retries,
        modelCalls: a.modelCalls,
        toolCalls: a.toolCalls,
        filesChanged: exactFiles ?? a.files.size,
      },
      filesApproximate: a.filesApprox && exactFiles == null,
      costUsd: Number(a.cost.toFixed(9)),
      unpricedCalls: a.unpriced,
      unsettledCalls: a.unsettled,
      inputTokens: a.inTok,
      outputTokens: a.outTok,
      elapsedMs: a.maxOffset,
      firstErrorIndex: this.firstErr,
      firstErrorReached: this.firstErr >= 0 && index >= this.firstErr,
      statusText: statusText(a, this.ctx.status),
      currentAgent: so[index]?.agent_id ?? null,
      openSpans: [...a.open.values()],
      finishedSpans: this.log.slice(0, a.finishedLen),
    };
  }
}

/** The state as of event `index`, computed from scratch (a convenience over a fresh `ReplayProjection`). */
export function projectAt(events: readonly EventOut[], index: number, ctx: ReplayCtx): ReplayState {
  return new ReplayProjection(events, ctx).advance(index);
}
