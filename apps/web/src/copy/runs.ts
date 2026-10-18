import type { AnalyticsSummary } from "@/lib/api/types";
import { formatCost } from "@/lib/format";

/** Strings for the Runs home (05-microcopy-and-states.md section 3). */
export const runs = {
  title: "Runs",
  compare: "Compare",
  loading: "Loading runs",
  loadMore: "Load more",
  loadingMore: "Loading…",
  summary: {
    window: "Last 7 days",
    analytics: "Analytics",
    analyticsHint: "Cost and reliability analytics",
  },
  search: {
    label: "Search runs",
    placeholder: "Search runs, for example status:failed",
    help: "Search help",
    helpIntro:
      "Terms are combined with AND. Prefix a term with - to exclude it, quote phrases, end a value with * for a prefix. Times can be relative, for example started:>=-24h.",
    fieldsLabel: "Searchable fields",
    fieldsLoading: "Loading the searchable fields…",
    chipsLabel: "Search terms",
    remove: (text: string) => `Remove ${text}`,
    clear: "Clear filters",
    badAgent: "not a valid agent name, ignored",
  },
  count: (n: number, end: boolean) =>
    `${n} run${n === 1 ? "" : "s"} loaded${end ? " (end of list)" : ""}`,
  facets: {
    label: "Narrow the results",
    summary: "Narrow results",
    on: (n: number) => `${n} on`,
    attention: "Needs attention",
    attentionHint: "Failed, timed out and blocked runs",
    skipped: "Counts were left out to keep this search fast. Narrow the query to see them.",
    more: "More fields",
    status: "Status",
    agent: "Agent",
    model: "Model",
    started: "Started",
    signals: "Errors and retries",
    tools: "Tools",
    eventTypes: "Event types",
  },
  table: {
    caption: "Runs",
    status: "Status",
    run: "Run",
    agent: "Agent",
    started: "Started",
    duration: "Duration",
    cost: "Cost",
    matched: "Matched",
    findings: "Findings",
    pick: "Pick to compare",
    details: (open: boolean, name: string) => `${open ? "Hide" : "Show"} details of ${name}`,
    retries: (n: number) => `${n} retr${n === 1 ? "y" : "ies"}`,
  },
  compareBar: {
    none: (n: number) => `Tick ${n} runs to compare them.`,
    some: (k: number, n: number) => `${k} of ${n} runs picked.`,
    go: "Compare the two runs",
    one: "Pick one more run.",
    clear: "Clear picks",
  },
  empty: {
    noMatchTitle: "No runs match these filters",
    noMatchBody: "Try clearing a filter.",
  },
  guide: {
    title: "No runs yet",
    lede: "Record one agent run in three steps. This page switches to it when the first event arrives.",
    keyTitle: "Create an API key",
    keyAllowed: "Create a key in settings",
    keyAllowedAfter: "and copy it at once: the token is shown only once.",
    keyDeniedBefore: "Ask a workspace owner or admin for a key (",
    keyDeniedLink: "API keys",
    keyDeniedAfter: "). The token is shown only once, when it is created.",
    installTitle: "Install the SDK",
    installCopy: "Copy the install command",
    checkout: "Working from a checkout of this repository? Run",
    recordTitle: "Record a run",
    recordBefore: "Set",
    recordMid: "to your key (and",
    recordMid2: "if the API is not on",
    recordAfter: "), then run:",
    snippetCopy: "Copy the example code",
    copy: "Copy",
    copied: "Copied",
    copyFailed: "Could not copy; select the text",
    waiting: "Waiting for your first event",
    arrived: "Your first run arrived.",
    open: "Open the run",
    showAll: "Show all runs",
  },
} as const;

export type WeekParts = {
  runs: number;
  succeededPct: number | null;
  failed: number;
  spend: string;
  active: number;
};

/** The numbers the 7-day sentence is made of, from the analytics summary the server computed over the whole window. */
export function weekParts(d: AnalyticsSummary): WeekParts {
  const finished = d.runs.finished;
  return {
    runs: d.runs.total,
    succeededPct: finished > 0 ? Math.round((d.runs.success / finished) * 100) : null,
    failed: d.runs.failed + d.runs.timed_out + d.runs.blocked,
    spend: formatCost(d.cost.total_usd),
    active: d.runs.active,
  };
}

const n = (v: number) => v.toLocaleString("en-US");

/**
 * "Last 7 days: 12 runs, 83% succeeded, 3 failed, $22.15 spent, 2 in progress." The optional pieces (`extra`) are the ones a
 * phone drops, so the short form is "Last 7 days: 12 runs, 3 failed, timed out or were blocked." Counts are words: zero says "none failed", not "0".
 */
export function weekSentence(p: WeekParts): {
  lead: string;
  extra: string[];
  failed: string;
  tail: string[];
} {
  return {
    lead: `${runs.summary.window}: ${n(p.runs)} run${p.runs === 1 ? "" : "s"}`,
    extra: p.succeededPct === null ? [] : [`${p.succeededPct}% succeeded`],
    failed:
      p.failed === 0
        ? "none failed, timed out or were blocked"
        : `${n(p.failed)} failed, timed out or were blocked`,
    tail: [`${p.spend} spent`, ...(p.active > 0 ? [`${n(p.active)} in progress`] : [])],
  };
}

/** The same sentence as plain text (tests and the accessible name). */
export function weekText(p: WeekParts): string {
  const s = weekSentence(p);
  return `${[s.lead, ...s.extra, s.failed, ...s.tail].join(", ")}.`;
}
