import type { EventOut, RunOut } from "@/lib/api/types";
import { formatCost, formatDuration, formatRelative, num } from "./format";
import { leftText, pendingApproval } from "./policy";
import { costDisplay } from "./replay/figures";
import type { ReplayState } from "./replay/project";
import { firstErrorFor, isError, isRetry } from "./timeline";

/**
 * What the run page's headline says, as plain data (ADR-111, Phase 22 D4). The component only lays it out, so the words
 * are testable without a DOM. Everything here comes from event metadata (types, statuses, timestamps, tool and model
 * names, error types, counts, cost), never from content attributes, so a viewer's headline says the same as an owner's.
 */

export type HeadlineKind = "failed" | "running" | "waiting" | "recovered" | "success" | "cancelled";
export const HEADLINE_KINDS: readonly HeadlineKind[] = [
  "failed",
  "running",
  "waiting",
  "recovered",
  "success",
  "cancelled",
];

export type Failure = {
  eventId: string;
  /** The first error's `sequence` (or its place in the loaded events); null when neither is known. */
  step: number | null;
  /** Milliseconds from the run's first event; null while the event itself is not loaded. */
  offsetMs: number | null;
  subject: string;
  errorType: string | null;
  /** Some of the event's attributes are withheld for this role; the headline never needed them. */
  withheld: boolean;
};

/** What the run did after its first error: the "cost of the failure". */
export type After = {
  retries: number;
  seconds: number | null;
  costUsd: number | null;
  unpricedCalls: number;
};

export type HeadlineModel = {
  kind: HeadlineKind;
  /** The sentence in the headline's fixed place. */
  lead: string;
  /** The mono line under it. Empty while the figures it needs are still loading. */
  detail: string;
  /** The facts for `detail` are still loading (an older run whose events are paging in); lay out a skeleton, not "0". */
  pending: boolean;
  failure: Failure | null;
  /** A live run: the headline carries the recording dot. */
  live: boolean;
};

const pluralise = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const retriesText = (n: number) => (n === 0 ? "No retries" : pluralise(n, "retry", "retries"));
const stepText = (step: number | null) => (step == null ? "" : ` step ${step}`);

const attr = (e: EventOut, k: string): string | null => {
  const v = e.attributes[k];
  return typeof v === "string" && v ? v : null;
};

/** The run's own closing events: when one of them is the first error, the run itself ended there. */
const isRunEnd = (e: EventOut) => e.event_type === "run.completed" || e.event_type === "run.failed";

/**
 * "Tool or event": the tool or model that failed, else the event's kind in words. Metadata only, so it is never
 * withheld, and never an attribute outside the reviewed metadata (tool, model and error type names).
 */
function subjectOf(e: EventOut): string {
  return (
    attr(e, "tool.name") ??
    attr(e, "llm.model") ??
    (isRunEnd(e) ? "the run's end" : e.event_type.replace(/[._]/g, " "))
  );
}
function errorTypeOf(e: EventOut): string | null {
  const named = attr(e, "tool.error_type") ?? attr(e, "llm.error_type");
  if (named) return named;
  // The status of a run's end or a policy block only repeats the lead's own word ("Timed out", "Blocked").
  return isRunEnd(e) || e.status === "blocked" ? null : (e.status ?? null);
}

const costOf = (e: EventOut): number | null => num(e.attributes["cost.estimated_usd"]);

/** The "after" figures from loaded events; correct only when every event is loaded. */
function afterFromEvents(run: RunOut, events: readonly EventOut[], index: number): After {
  const err = events[index]!;
  const later = events.slice(index + 1);
  const last = events[events.length - 1]!;
  const end = run.completed_at ? Date.parse(run.completed_at) : Date.parse(last.occurred_at);
  const calls = later.filter((e) => e.event_type === "llm.request.completed");
  const seconds = Math.max(0, (end - Date.parse(err.occurred_at)) / 1000);
  return {
    retries: later.filter(isRetry).length,
    seconds: Number.isFinite(seconds) ? seconds : null,
    costUsd: Number(calls.reduce((s, e) => s + (costOf(e) ?? 0), 0).toFixed(6)),
    unpricedCalls: calls.filter((e) => costOf(e) == null).length,
  };
}

/**
 * The first error and its cost. The server's stored facts (ADR-112) win when the run carries them and its summary is
 * current; older runs (null `first_error`) and runs whose summary is catching up fall back to the loaded events. In
 * that case the "after" figures are only trustworthy once every event has loaded, so they are null until then.
 */
export function deriveFailure(
  run: RunOut,
  events: readonly EventOut[],
  t0: number,
  eventsComplete: boolean,
): { failure: Failure | null; after: After | null } {
  const server = run.summary_state === "current" ? (run.first_error ?? null) : null;
  const loaded = firstErrorFor(run.status, events);
  const eventId = server?.event_id ?? loaded?.event_id ?? null;
  if (!eventId) return { failure: null, after: null };
  const index = events.findIndex((e) => e.event_id === eventId);
  const e = index >= 0 ? events[index]! : null;
  const failure: Failure = {
    eventId,
    step: server?.sequence ?? e?.sequence ?? (index >= 0 ? index + 1 : null),
    offsetMs: e ? Date.parse(e.occurred_at) - t0 : null,
    subject: e ? subjectOf(e) : "an event",
    errorType: e ? errorTypeOf(e) : null,
    withheld: (e?.withheld_attributes?.length ?? 0) > 0,
  };
  if (server) {
    return {
      failure,
      after: {
        retries: server.retries_after,
        seconds: server.seconds_after,
        costUsd: server.cost_after_usd,
        unpricedCalls: server.cost_after_unpriced_calls,
      },
    };
  }
  return {
    failure,
    after: eventsComplete && index >= 0 ? afterFromEvents(run, events, index) : null,
  };
}

export function headlineKind(run: RunOut, hasError: boolean): HeadlineKind {
  switch (run.status) {
    case "FAILED":
    case "TIMED_OUT":
    case "BLOCKED":
      return "failed";
    case "CANCELLED":
      return "cancelled";
    case "WAITING":
    case "WAITING_FOR_APPROVAL":
      return "waiting";
    case "SUCCESS":
      return hasError || (num(run.summary["retry_count"]) ?? 0) > 0 ? "recovered" : "success";
    default:
      return "running";
  }
}

const afterText = (a: After | null): string => {
  if (!a) return "";
  const time = a.seconds == null ? null : formatDuration(a.seconds * 1000);
  const cost = formatCost(a.costUsd);
  const unpriced =
    a.unpricedCalls > 0 ? ` (${pluralise(a.unpricedCalls, "call", "calls")} unpriced)` : "";
  const spent =
    time && a.costUsd != null
      ? `${time} and ${cost} spent after${unpriced}`
      : a.costUsd != null
        ? `${cost} spent after${unpriced}`
        : time
          ? `${time} after`
          : "";
  return spent;
};

const join = (parts: readonly (string | null | false | undefined)[]) =>
  parts.filter(Boolean).join(" · ");

const subjectLine = (f: Failure) => (f.errorType ? `${f.subject}, ${f.errorType}` : f.subject);
const offsetText = (f: Failure) =>
  f.offsetMs == null ? "" : ` (+${formatDuration(Math.max(0, f.offsetMs))})`;

/** Test results the run reported, if any: the last event carrying a total. */
function testsLine(events: readonly EventOut[]): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const a = events[i]!.attributes;
    if (num(a["test.total"]) == null) continue;
    const failed = num(a["test.failed"]) ?? 0;
    return failed ? `${pluralise(failed, "test", "tests")} failed` : "tests pass";
  }
  return null;
}

/** Why a cancelled run stopped, by kind only: what was denied or why it was cancelled is free text (review P2-1). */
function cancelledBy(events: readonly EventOut[]): string | null {
  return events.some((e) => e.event_type === "approval.denied")
    ? "Cancelled after an approval was denied"
    : null;
}

export function headlineModel(input: {
  run: RunOut;
  events: readonly EventOut[];
  t0: number;
  eventsComplete: boolean;
  now?: number;
  /**
   * What the run is waiting on, when the viewer may read approvals: the real time left (seconds, already aged to now) from
   * `GET /v1/approvals/{id}`. Without it the headline names the capability from the event but states no time.
   */
  approvalLeftSeconds?: number | null;
}): HeadlineModel {
  const { run, events, t0, eventsComplete } = input;
  const now = input.now ?? Date.now();
  const { failure, after } = deriveFailure(run, events, t0, eventsComplete);
  const kind = headlineKind(run, failure != null);
  const s = run.summary;
  const total = formatCost(num(s["estimated_cost_usd"]));
  const retries = num(s["retry_count"]) ?? 0;
  const duration = formatDuration(run.duration_ms ?? num(s["duration_ms"]));
  const last = events[events.length - 1];
  const lastStep = last?.sequence ?? (events.length || null);
  const pending = failure != null && after == null && (kind === "failed" || kind === "recovered");
  const common = { kind, failure, pending };

  switch (kind) {
    case "failed": {
      const word =
        run.status === "TIMED_OUT" ? "Timed out" : run.status === "BLOCKED" ? "Blocked" : "Failed";
      return {
        ...common,
        live: false,
        lead: failure
          ? `${word} at${stepText(failure.step) || " the first error"}${offsetText(failure)}: ${subjectLine(failure)}`
          : `${word} after ${duration}`,
        detail: failure
          ? pending
            ? ""
            : join([after && retriesText(after.retries), afterText(after), `${total} total`])
          : `${total} total`,
      };
    }
    case "recovered":
      return {
        ...common,
        live: false,
        lead:
          retries > 0
            ? `Succeeded after ${pluralise(retries, "retry", "retries")}`
            : "Succeeded after recovering from an error",
        detail: pending
          ? ""
          : failure
            ? join([
                `Recovered from ${subjectLine(failure)}${failure.step == null ? "" : ` at step ${failure.step}`}`,
                after &&
                  after.costUsd != null &&
                  `${formatCost(after.costUsd)} spent on the recovery`,
                `${total} total`,
              ])
            : `${total} total`,
      };
    case "success": {
      const files = num(s["files_modified"]) ?? 0;
      return {
        ...common,
        live: false,
        lead: `Completed in ${duration}, ${total}`,
        detail:
          join([files > 0 && `${pluralise(files, "file", "files")} changed`, testsLine(events)]) ||
          "No errors or retries",
      };
    }
    case "running": {
      const age = last ? ` · last event ${formatRelative(last.occurred_at, now)}` : "";
      return {
        ...common,
        live: true,
        lead: `${run.status === "QUEUED" ? "Queued" : "Running"}${lastStep == null ? "" : ` · step ${lastStep}`}${age}`,
        detail: join([
          `${total} so far`,
          failure && `first error at${stepText(failure.step)}: ${subjectLine(failure)}`,
          retries > 0 && pluralise(retries, "retry", "retries"),
        ]),
      };
    }
    case "waiting": {
      const ask = [...events].reverse().find((e) => e.event_type === "approval.requested");
      const since = ask ?? last;
      const wait = since ? formatDuration(Math.max(0, now - Date.parse(since.occurred_at))) : null;
      // Never the approval's action or the last event's content: a viewer reads the same words as an owner. The
      // capability (`github.push`) is event metadata the policy service wrote, so every role may read it.
      const capability = pendingApproval(events)?.capability ?? null;
      const what =
        run.status === "WAITING_FOR_APPROVAL"
          ? capability
            ? `Waiting for approval: ${capability}`
            : "Awaiting approval"
          : `Waiting${last ? ` on ${subjectOf(last)}` : ""}`;
      const left =
        run.status === "WAITING_FOR_APPROVAL" ? leftText(input.approvalLeftSeconds ?? null) : null;
      return {
        ...common,
        live: false,
        lead: `${what}${lastStep == null ? "" : ` · step ${lastStep}`}`,
        detail: join([left, wait && wait !== "—" && `waiting ${wait}`, `${total} so far`]),
      };
    }
    default:
      return {
        ...common,
        live: false,
        lead: `Cancelled${lastStep == null ? "" : ` at step ${lastStep}`} after ${duration}`,
        detail: join([cancelledBy(events), `${total} total`]),
      };
  }
}

/**
 * The headline as of a replay playhead (Phase 12). It must not leak the future: before the first error is reached it
 * reads as a run in progress ("Running · step 4 · replay"), whatever the run's final status; once reached it names the
 * first error (a fact about the past by then) and only the figures so far. The run's outcome, "recovered" or "failed",
 * is never stated, because at that moment nobody knows it yet. `live` supplies the first error's facts; `s` the figures.
 */
export function replayHeadline(live: HeadlineModel, s: ReplayState): HeadlineModel {
  // A figure the replay cannot state exactly says so: "at least $0.42 so far · 3 calls unpriced".
  const spend = costDisplay(s);
  const cost = join([`${spend.text} so far`, spend.note]);
  const retries = s.counts.retries;
  const f = live.failure;
  if (s.firstErrorReached && f) {
    // A blocked run's "first error" can be the policy block itself, which is not an error event (ADR-112): say what it is.
    const at = s.events[s.firstErrorIndex];
    const blockedStop = at != null && !isError(at);
    return {
      kind: "failed",
      lead: blockedStop
        ? `Blocked at${stepText(f.step) || " the blocking event"}${offsetText(f)}: ${subjectLine(f)}`
        : `First error at${stepText(f.step) || " the first error"}${offsetText(f)}: ${subjectLine(f)}`,
      detail: join([retriesText(retries), cost]),
      pending: false,
      failure: f,
      live: false,
    };
  }
  // "Running", "Completed", "Waiting for approval": the state's own word, without its ", n errors so far" tail.
  const word = s.statusText.split(",")[0] ?? "Running";
  const step = s.current?.sequence ?? (s.index >= 0 ? s.index + 1 : null);
  return {
    kind: "running",
    lead: `${word}${step == null ? "" : ` · step ${step}`} · replay`,
    detail: join([cost, retries > 0 && pluralise(retries, "retry", "retries")]),
    pending: false,
    failure: null,
    live: false,
  };
}
