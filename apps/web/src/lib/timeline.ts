import type { EventOut } from "@/lib/api/types";
import { formatCost, formatDuration, formatInt, num } from "./format";

/** Pure timeline logic: classification, filtering, grouping, causal-error detection. Rendering lives elsewhere. */

export type EventClass =
  "run" | "llm" | "tool" | "file" | "shell" | "reliability" | "approval" | "other";

export const CLASS_ORDER: readonly EventClass[] = [
  "run",
  "llm",
  "tool",
  "file",
  "shell",
  "reliability",
  "approval",
  "other",
];
export const CLASS_LABELS: Record<EventClass, string> = {
  run: "Run & agent",
  llm: "Model calls",
  tool: "Tools",
  file: "Files & git",
  shell: "Shell",
  reliability: "Retries & limits",
  approval: "Approvals & policy",
  other: "Other",
};

export function eventClass(type: string): EventClass {
  const family = type.split(".")[0] ?? "";
  switch (family) {
    case "run":
    case "agent":
      return "run";
    case "llm":
    case "tool":
    case "shell":
      return family;
    case "file":
    case "git":
      return "file";
    case "retry":
    case "timeout":
    case "loop":
    case "rate_limit":
      return "reliability";
    case "approval":
    case "policy":
      return "approval";
    default:
      return "other";
  }
}

export const isError = (e: EventOut): boolean =>
  e.status === "error" || e.status === "timeout" || e.event_type.endsWith(".failed");
export const isRetry = (e: EventOut): boolean => e.event_type === "retry.attempted";

/**
 * The first error in canonical order. Events are already ordered by the API (never re-sorted here: the order is the
 * product's evidence), so "first" is the earliest failure a reader should look at before any consequence of it.
 */
export const firstError = (events: readonly EventOut[]): EventOut | undefined =>
  events.find(isError);

const attr = (e: EventOut, k: string): string | null => {
  const v = e.attributes[k];
  return typeof v === "string" && v ? v : null;
};

/** One-line human summary of an event; text only (payloads and attributes are untrusted). */
export function describe(e: EventOut): string {
  const a = e.attributes;
  switch (e.event_type) {
    case "llm.request.completed": {
      const parts = [attr(e, "llm.model") ?? "model"];
      const i = num(a["llm.input_tokens"]);
      const o = num(a["llm.output_tokens"]);
      if (i != null || o != null) parts.push(`${formatInt(i)} in / ${formatInt(o)} out`);
      const c = num(a["cost.estimated_usd"]);
      if (c != null) parts.push(formatCost(c));
      return parts.join(" · ");
    }
    case "llm.request.started":
    case "llm.request.failed":
      return attr(e, "llm.model") ?? "model call";
    case "retry.attempted": {
      const n = num(a["retry.attempt"]);
      const why = attr(e, "retry.reason");
      return `Retry${n != null ? ` #${n}` : ""}${why ? ` after ${why}` : ""}`;
    }
    case "run.started":
      return attr(e, "run.name") ?? "Run started";
    default:
  }
  if (e.event_type.startsWith("tool.")) {
    const name = attr(e, "tool.name") ?? "tool";
    const err = attr(e, "tool.error_type");
    return err ? `${name} · ${err}` : name;
  }
  if (e.event_type.startsWith("file.")) {
    const added = num(a["file.lines_added"]);
    const removed = num(a["file.lines_removed"]);
    const delta = added != null || removed != null ? ` +${added ?? 0} -${removed ?? 0}` : "";
    return `${attr(e, "file.path") ?? "file"}${delta}`;
  }
  if (e.event_type.startsWith("git.")) {
    return (
      attr(e, "git.push_target") ??
      attr(e, "git.branch") ??
      attr(e, "git.commit_hash")?.slice(0, 10) ??
      ""
    );
  }
  if (e.event_type.startsWith("shell.")) {
    const cmd = attr(e, "shell.command") ?? "command";
    const code = num(a["shell.exit_code"]);
    const failed = num(a["test.failed"]);
    const tests =
      num(a["test.total"]) != null
        ? ` · ${failed ? `${failed} test(s) failed` : "tests pass"}`
        : "";
    return code != null ? `${cmd} · exit ${code}${tests}` : cmd;
  }
  return attr(e, "error.summary") ?? attr(e, "approval.action") ?? "";
}

export type Filters = {
  /** null = every class */
  classes: ReadonlySet<EventClass> | null;
  errorsOnly: boolean;
};
export const NO_FILTERS: Filters = { classes: null, errorsOnly: false };

export function filterEvents(events: readonly EventOut[], f: Filters): EventOut[] {
  return events.filter((e) => {
    if (f.errorsOnly && !isError(e)) return false;
    if (f.classes && !f.classes.has(eventClass(e.event_type))) return false;
    return true;
  });
}

export type EventRow = { kind: "event"; key: string; event: EventOut; indented: boolean };
export type GroupRow = {
  kind: "group";
  key: string;
  label: string;
  count: number;
  status: string | null;
  durationMs: number | null;
  hasError: boolean;
  collapsed: boolean;
  first: EventOut;
};
export type Row = EventRow | GroupRow;

const groupLabel = (first: EventOut): string => {
  const base = first.event_type.replace(/\.(started|completed|failed)$/, "");
  const who = attr(first, "tool.name") ?? attr(first, "llm.model") ?? attr(first, "shell.command");
  return who ? `${base} · ${who}` : base;
};

/**
 * Collapse grouping: consecutive events sharing a span (e.g. a tool call's started + completed) form one collapsible
 * group. Only consecutive events group, so ordering is never changed by grouping.
 */
export function buildRows(events: readonly EventOut[], collapsed: ReadonlySet<string>): Row[] {
  const rows: Row[] = [];
  let i = 0;
  while (i < events.length) {
    const first = events[i]!;
    let j = i + 1;
    if (first.span_id) while (j < events.length && events[j]!.span_id === first.span_id) j++;
    const members = events.slice(i, j);
    if (members.length < 2) {
      rows.push({ kind: "event", key: first.event_id, event: first, indented: false });
    } else {
      const key = `g:${first.event_id}`;
      const isCollapsed = collapsed.has(key);
      const last = members[members.length - 1]!;
      rows.push({
        kind: "group",
        key,
        label: groupLabel(first),
        count: members.length,
        status: members.some(isError) ? "error" : (last.status ?? null),
        durationMs: members.map((m) => m.duration_ms).find((d) => d != null) ?? null,
        hasError: members.some(isError),
        collapsed: isCollapsed,
        first,
      });
      if (!isCollapsed) {
        for (const m of members)
          rows.push({ kind: "event", key: m.event_id, event: m, indented: true });
      }
    }
    i = j;
  }
  return rows;
}

export const groupKeys = (rows: readonly Row[]): string[] =>
  rows.filter((r): r is GroupRow => r.kind === "group").map((r) => r.key);

export const describeDuration = (e: EventOut): string => formatDuration(e.duration_ms);
