import type { DimensionOut, FindingOut, FindingsBadge, RunFindingsOut } from "./api/types";
import { formatInt } from "./format";

/**
 * Presentation logic for findings, kept out of the components: wording, ordering, and a safe reading of the typed
 * evidence. Evidence is metadata by construction (ADR-080), but it is still API data: every value is checked before
 * use and rendered as text by the caller.
 */

export type Severity = FindingOut["severity"];
export type DimStatus = DimensionOut["status"];
export type Tone = "ok" | "bad" | "warn" | "info" | "neutral";

const SEVERITY: Record<Severity, { glyph: string; label: string; tone: Tone; rank: number }> = {
  critical: { glyph: "✕", label: "Critical", tone: "bad", rank: 2 },
  warning: { glyph: "▲", label: "Warning", tone: "warn", rank: 1 },
  info: { glyph: "ⓘ", label: "Info", tone: "info", rank: 0 },
};
export const severityMeta = (s: string) =>
  SEVERITY[s as Severity] ?? { glyph: "?", label: s, tone: "neutral" as Tone, rank: -1 };

const HEALTH: Record<DimStatus, { glyph: string; label: string; tone: Tone }> = {
  ok: { glyph: "✓", label: "OK", tone: "ok" },
  warn: { glyph: "▲", label: "Warning", tone: "warn" },
  bad: { glyph: "✕", label: "Bad", tone: "bad" },
  no_data: { glyph: "–", label: "No data", tone: "neutral" },
};
export const healthMeta = (s: string) =>
  HEALTH[s as DimStatus] ?? { glyph: "?", label: s, tone: "neutral" as Tone };

export const DIMENSION_LABELS: Record<string, string> = {
  reliability: "Reliability",
  tool_reliability: "Tool reliability",
  cost_efficiency: "Cost efficiency",
  loops: "Loops",
  context: "Context",
};
export const dimensionLabel = (name: string): string => DIMENSION_LABELS[name] ?? name;

/** "Probable" interpretations are named in words; typed facts are "Observed". Never colour alone. */
export const kindLabel = (kind: string): "Probable" | "Observed" =>
  kind === "probable" ? "Probable" : "Observed";

/** Most severe first, then in the order the API gave (it sorts by detector and position). */
export function sortFindings(items: readonly FindingOut[]): FindingOut[] {
  return items
    .map((f, i) => ({ f, i }))
    .sort((a, b) => severityMeta(b.f.severity).rank - severityMeta(a.f.severity).rank || a.i - b.i)
    .map(({ f }) => f);
}

/** The event a "Show evidence" jump lands on. */
export const evidenceTarget = (f: FindingOut): string | null =>
  f.start_event_id ?? f.evidence_event_ids[0] ?? f.end_event_id ?? null;

export type StateLine = { text: string; tone: Tone };

/** One honest sentence about how fresh the analysis is. */
export function stateLine(state: RunFindingsOut["state"], runActive: boolean): StateLine {
  const seen = state.event_count_seen;
  const over = seen != null ? ` over ${formatInt(seen)} events` : "";
  const when = state.computed_at ? ` at ${new Date(state.computed_at).toISOString()}` : "";
  switch (state.status) {
    case "none":
      return { text: "Not analysed yet.", tone: "neutral" };
    case "processing":
      return {
        text: state.computed_at ? `Analysing… showing the previous analysis${over}.` : "Analysing…",
        tone: "info",
      };
    case "outdated":
      return {
        text: `Detector rules changed since this analysis${over}; the findings may be outdated.`,
        tone: "warn",
      };
    case "failed":
      return {
        text: state.computed_at
          ? `The latest analysis failed and needs an operator. Showing the last successful analysis${when}${over}.`
          : "The latest analysis failed and needs an operator.",
        tone: "bad",
      };
    default: {
      const cut =
        state.truncated && seen != null
          ? ` Analysed the first ${formatInt(seen)} events only; the run is longer.`
          : "";
      return {
        text: `${runActive ? "Analysis is up to date for the events so far." : "Analysis is current."}${cut}`,
        tone: "neutral",
      };
    }
  }
}

// ---------------------------------------------------------------- evidence

type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : null;
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const text = (v: unknown): string | null => (typeof v === "string" && v ? v : null);
const int = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export type EvidenceStep = {
  eventId: string;
  /** Short description such as "root · tool.call.failed". */
  label: string;
};
export type EvidenceSummary = {
  chips: string[];
  facts: [string, string][];
  /** Ordered events the finding cites with a role or type (the failure chain, cascade failures). */
  steps: EvidenceStep[];
  stepsTitle: string | null;
  /** The recorded thresholds, so a reader can see what rule fired. */
  thresholds: [string, string][];
};

const fmt = (v: unknown): string => {
  if (typeof v === "number") return Number.isInteger(v) ? formatInt(v) : String(+v.toFixed(2));
  if (typeof v === "boolean") return v ? "yes" : "no";
  return typeof v === "string" ? v : "—";
};

/** Seconds for durations in evidence (ms in, short text out). */
const ms = (v: unknown): string => {
  const n = int(v);
  if (n == null) return "—";
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)} s` : `${Math.round(n)} ms`;
};

export function summarizeEvidence(
  f: Pick<FindingOut, "finding_type" | "evidence">,
): EvidenceSummary {
  const e = obj(f.evidence) ?? {};
  const out: EvidenceSummary = {
    chips: [],
    facts: [],
    steps: [],
    stepsTitle: null,
    thresholds: [],
  };
  const add = (label: string, v: unknown) => {
    if (v != null && v !== "") out.facts.push([label, fmt(v)]);
  };
  const agent = text(e["agent_id"]);
  if (agent) add("Agent", agent);
  for (const [k, v] of Object.entries(obj(e["thresholds"]) ?? {})) {
    out.thresholds.push([k.replace(/_/g, " "), fmt(v)]);
  }

  switch (f.finding_type) {
    case "possible_loop":
    case "reported_loop":
      out.chips = list(e["pattern"]).map((p) => text(p) ?? "?");
      add("Repetitions", int(e["repetitions"]));
      if (e["duration_ms"] != null) out.facts.push(["Duration", ms(e["duration_ms"])]);
      break;
    case "identical_request":
      add("Event type", text(e["event_type"]));
      add("Tool", text(e["tool_name"]));
      add("Identical calls", int(e["count"]));
      break;
    case "retry_storm":
      add("Retries", int(e["retries"]));
      add("Without an operation", int(e["retries_unattributed"]));
      add("In a burst", e["burst"]);
      add("Rate-limit hits", int(e["rate_limit_hits"]));
      for (const s of list(e["by_scope"])) {
        const o = obj(s);
        const id = o && text(o["first_event_id"]);
        if (o && id)
          out.steps.push({ eventId: id, label: `${fmt(o["count"])} retries on one operation` });
      }
      out.stepsTitle = "Where the retries were";
      break;
    case "context_growth":
      add("Model calls", int(e["calls"]));
      add("First input tokens", int(e["first_tokens"]));
      add("Last input tokens", int(e["last_tokens"]));
      add("Growth", e["growth_factor"] != null ? `${fmt(e["growth_factor"])}x` : null);
      add("Mean growth per call", int(e["mean_delta_tokens"]));
      add("Accelerating", e["superlinear"]);
      break;
    case "cost_spike": {
      const b = obj(e["baseline"]) ?? {};
      add("This run", text(e["cost_usd"]) ? `$${text(e["cost_usd"])}` : null);
      add("Baseline runs", int(b["n"]));
      add("Median", text(b["median_usd"]) ? `$${text(b["median_usd"])}` : null);
      add("p95", text(b["p95_usd"]) ? `$${text(b["p95_usd"])}` : null);
      add("Window (days)", int(b["window_days"]));
      break;
    }
    case "tool_failure_cascade":
      add("Calls in window", int(e["calls_in_window"]));
      add("Retries between", int(e["retries_between"]));
      out.chips = list(e["alternates"]).map((a) => text(a) ?? "?");
      for (const s of list(e["failures"])) {
        const o = obj(s);
        const id = o && text(o["event_id"]);
        if (o && id) {
          const what = [text(o["tool_name"]), text(o["error_type"])].filter(Boolean).join(" · ");
          out.steps.push({
            eventId: id,
            label: `${text(o["event_type"]) ?? "failure"}${what ? ` · ${what}` : ""}`,
          });
        }
      }
      out.stepsTitle = "Failures";
      break;
    case "failure_chain":
      add("Category", text(e["category"]));
      add("Retries after the first error", int(e["retries_caused"]));
      add("Retries without an operation", int(e["retries_unattributed"]));
      add("Changes before the end", int(e["mutations"]));
      for (const s of list(e["chain"])) {
        const o = obj(s);
        const id = o && text(o["event_id"]);
        if (o && id) {
          out.steps.push({
            eventId: id,
            label: `${text(o["role"]) ?? "event"} · ${text(o["event_type"]) ?? "?"}`,
          });
        }
      }
      out.stepsTitle = "Chain of events";
      break;
    case "probable_cause":
      add("Tool", text(e["tool_name"]));
      add("Model", text(e["model"]));
      break;
    default:
      break;
  }
  return out;
}

// ---------------------------------------------------------------- runs list

export type CellView = {
  /** Text next to the glyph; empty when there is nothing to show. */
  visible: string;
  glyph: string;
  tone: Tone;
  state: string;
  /** Always set: the plain-words reading, for screen readers and the tooltip. */
  title: string;
};

/** The runs-list cell: count and worst severity as text; "—" when none or not analysed. */
export function findingsCell(badge: FindingsBadge | null | undefined): CellView {
  if (!badge || badge.state === "none") {
    return { visible: "", glyph: "", tone: "neutral", state: "none", title: "Not analysed yet" };
  }
  if (badge.count === 0) {
    const stale = badge.state === "outdated" ? " (detector rules changed since)" : "";
    return {
      visible: "",
      glyph: "",
      tone: "neutral",
      state: badge.state,
      title: `No findings${stale}`,
    };
  }
  const sev = severityMeta(badge.max_severity ?? "info");
  const word = badge.count === 1 ? "finding" : "findings";
  const stale = badge.state === "outdated" ? ", may be outdated" : "";
  return {
    visible: `${badge.count} · ${sev.label.toLowerCase()}${badge.state === "outdated" ? " (outdated)" : ""}`,
    glyph: sev.glyph,
    tone: sev.tone,
    state: badge.state,
    title: `${badge.count} ${word}, most severe: ${sev.label.toLowerCase()}${stale}`,
  };
}
