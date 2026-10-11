import { ALL_STATUSES, type MatchedOut, type RunOut, type RunStatus } from "./api/types";
import { ApiRequestError } from "./api/client";

/**
 * Search helpers. The grammar lives in the API (spec §152): this file never parses `q`. It only edits the text the
 * person typed by the character spans the server echoed back (`query.terms[].span`), and classifies the API's refusals
 * so the page can explain them instead of showing "no results".
 */

export const AGENT_SLUG = /^[a-z0-9][a-z0-9._-]{0,63}$/;
/** The API's limit on `q` (characters); longer input is refused locally with the same wording as a 422. */
export const MAX_Q_CHARS = 512;

const SAFE_VALUE = /^[A-Za-z0-9_.\-/]+$/;

/** A grammar value for a facet click: bare when it is plain, quoted (with `\"` and `\\` escapes) otherwise. */
export function quoteValue(value: string): string {
  if (SAFE_VALUE.test(value)) return value;
  return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

/** `q` plus one more term, separated by a space. The server parses it again; nothing is validated here. */
export function appendTerm(q: string, term: string): string {
  const base = q.trim();
  return base ? `${base} ${term}` : term;
}

export function fieldTerm(field: string, value: string): string {
  return `${field}:${quoteValue(value)}`;
}

/** Removes the characters `[start, end)` (a span echoed by the server) and tidies the whitespace around them. */
export function removeSpan(q: string, span: readonly number[]): string {
  const [start, end] = span as [number, number];
  if (
    !Number.isInteger(start) ||
    !Number.isInteger(end) ||
    start < 0 ||
    end > q.length ||
    start >= end
  )
    return q;
  return `${q.slice(0, start)} ${q.slice(end)}`.replace(/\s+/g, " ").trim();
}

/** The exact text of a term, for its chip. Out-of-range spans (a stale echo) give an empty string. */
export function termText(q: string, span: readonly number[]): string {
  const [start, end] = span as [number, number];
  if (
    !Number.isInteger(start) ||
    !Number.isInteger(end) ||
    start < 0 ||
    end > q.length ||
    start >= end
  )
    return "";
  return q.slice(start, end);
}

/** The facet block's key -> the grammar field a click on it appends. */
export const FACET_FIELDS = {
  status: "status",
  agents: "agent",
  tools: "tool",
  models: "model",
  event_types: "event",
} as const;

export type LegacyQuery = {
  q: string;
  /** Statuses that stay a legacy parameter: the grammar is a conjunction and cannot say "FAILED or BLOCKED". */
  statuses: RunStatus[];
  /** An agent text the old URL carried that is not a valid slug (kept so the input can flag it). */
  agent: string;
};

/**
 * Old bookmarks carried `?status=FAILED&agent=x`; they become `q=status:failed agent:x` (plan F11). More than one
 * status stays a legacy parameter, which the API ORs and then ANDs with `q`.
 */
export function convertLegacy(sp: URLSearchParams): LegacyQuery {
  const typed = sp.get("q") ?? "";
  const listed = (sp.get("status")?.split(",") ?? []).filter((s): s is RunStatus =>
    (ALL_STATUSES as readonly string[]).includes(s),
  );
  const agent = sp.get("agent") ?? "";
  const terms: string[] = [];
  if (listed.length === 1) terms.push(`status:${listed[0]!.toLowerCase()}`);
  if (AGENT_SLUG.test(agent)) terms.push(`agent:${agent}`);
  const q = terms.reduce(appendTerm, typed.trim());
  return {
    q,
    statuses: listed.length > 1 ? listed : [],
    agent: AGENT_SLUG.test(agent) ? "" : agent,
  };
}

/** The relative time a `range` choice stands for; the server resolves it against its own clock on every request. */
const RANGE_TERM = { "1h": "-1h", "24h": "-24h", "7d": "-7d" } as const;
export const rangeTerm = (range: string): string | null =>
  range in RANGE_TERM ? `started:>=${RANGE_TERM[range as keyof typeof RANGE_TERM]}` : null;

/** What the person typed plus the range control, as the single `q` that is sent. */
export function effectiveQuery(q: string, range: string): string {
  const r = rangeTerm(range);
  return r ? appendTerm(q.trim(), r) : q.trim();
}

export type SearchProblem =
  | { kind: "invalid"; message: string; position: number | null }
  | { kind: "forbidden"; field: string | null }
  | { kind: "too-long" }
  | { kind: "busy" | "timeout"; message: string };

const FIELD_NAME = /^[a-z_]{1,32}$/;

/** Sorts an error from a search request into something the page can explain; `null` for every other failure. */
export function searchProblem(error: unknown): SearchProblem | null {
  if (!(error instanceof ApiRequestError)) return null;
  if (error.code === "QUERY_INVALID") {
    const pos = (error.details as { position?: unknown }).position;
    return {
      kind: "invalid",
      message: error.message,
      position: typeof pos === "number" && Number.isInteger(pos) && pos >= 0 ? pos : null,
    };
  }
  if (error.code === "PERMISSION_DENIED") {
    const field = (error.details as { field?: unknown }).field;
    return {
      kind: "forbidden",
      field: typeof field === "string" && FIELD_NAME.test(field) ? field : null,
    };
  }
  if (error.status === 414 || error.code === "QUERY_TOO_LONG") return { kind: "too-long" };
  if (error.code === "SEARCH_BUSY") return { kind: "busy", message: error.message };
  if (error.code === "SEARCH_TIMEOUT") return { kind: "timeout", message: error.message };
  return null;
}

export const matchedOf = (run: RunOut): MatchedOut | null =>
  (run.matched as MatchedOut | null | undefined) ?? null;

/** The values a run matched on, as `field:value` labels (display only). */
export function matchedLabels(m: MatchedOut): string[] {
  return [
    ...(m.agents ?? []).map((v) => `agent:${v}`),
    ...(m.tools ?? []).map((v) => `tool:${v}`),
    ...(m.models ?? []).map((v) => `model:${v}`),
    ...(m.event_types ?? []).map((v) => `event:${v}`),
    ...(m.tags ?? []).map((v) => `tag:${v}`),
  ];
}
