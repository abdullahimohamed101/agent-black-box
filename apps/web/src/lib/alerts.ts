import type { components } from "./api/schema";
import { ApiRequestError, fieldErrors } from "./api/client";
import { formatCost } from "./format";

/**
 * Presentation logic for alerts (Phase 17, ADR-150/151). An alert carries ids, names, a trigger, a severity and a status;
 * it never carries captured content, and nothing here asks for any. Every name that comes from the API (rule, project,
 * agent, destination) is returned as plain text for the components to render as text. Fixed vocabularies (trigger, error
 * code, skip reason) are mapped to words here; an unknown code from a newer API is shown as a generic phrase, never raw.
 */

export type AlertRule = components["schemas"]["RuleOut"];
export type AlertDestination = components["schemas"]["DestinationOut"];
export type AlertDestinationCreated = components["schemas"]["DestinationCreated"];
export type AlertNotification = components["schemas"]["NotificationOut"];
export type AlertTrigger = AlertRule["trigger"];
export type AlertFilters = components["schemas"]["RuleFilters"];
export type AlertSeverity = AlertNotification["severity"];
export type DestinationType = AlertDestination["type"];
export type DestinationState = AlertDestination["state"];
export type NotificationStatus = AlertNotification["status"];
export type RuleCreate = components["schemas"]["RuleCreate"];
export type RunStatusFilter = "FAILED" | "TIMED_OUT" | "BLOCKED";

export const ALERT_TABS = [
  { key: "rules", label: "Rules" },
  { key: "destinations", label: "Destinations" },
  { key: "history", label: "History" },
] as const;
export type AlertTab = (typeof ALERT_TABS)[number]["key"];
export const parseAlertTab = (v: string | null | undefined): AlertTab =>
  ALERT_TABS.find((t) => t.key === v)?.key ?? "rules";

export const TRIGGERS: readonly AlertTrigger[] = [
  "run_failed",
  "security_finding",
  "approval_requested",
  "approval_expiring",
  "spend_over",
];

/** "Tell me when ..." wording for a form option, and the shorter noun phrase for tables. */
export const TRIGGER_WORDS: Record<AlertTrigger, { when: string; short: string }> = {
  run_failed: { when: "a run fails", short: "A run fails" },
  security_finding: { when: "a security finding appears", short: "A security finding appears" },
  approval_requested: { when: "an agent asks for approval", short: "An approval is requested" },
  approval_expiring: { when: "an approval is about to expire", short: "An approval is about to expire" },
  spend_over: { when: "a day's spend goes over a limit", short: "Daily spend goes over a limit" },
}; // prettier-ignore
export const triggerShort = (t: string): string =>
  TRIGGER_WORDS[t as AlertTrigger]?.short ?? "An event";

export const SEVERITIES: readonly components["schemas"]["RuleFilters"]["min_severity"][] = [
  "info",
  "warning",
  "medium",
  "high",
  "critical",
];
const SEVERITY_WORD: Record<string, string> = {
  info: "Info",
  warning: "Warning",
  medium: "Medium",
  high: "High",
  critical: "Critical",
};
export const RUN_STATUS_WORD: Record<RunStatusFilter, string> = {
  FAILED: "failed",
  TIMED_OUT: "timed out",
  BLOCKED: "blocked",
};
export const RUN_STATUSES: readonly RunStatusFilter[] = ["FAILED", "TIMED_OUT", "BLOCKED"];

export const MAX_COOLDOWN_MINUTES = 10_080; // 7 days, the API's bound in minutes
export const MAX_LEAD_MINUTES = 1_440;

/** "5 minutes", "2 hours", "1 hour 30 minutes" from seconds (a whole number of seconds). */
export function durationWords(seconds: number): string {
  if (seconds <= 0) return "no wait";
  if (seconds < 60) return `${seconds} second${seconds === 1 ? "" : "s"}`;
  const d = Math.floor(seconds / 86_400);
  const h = Math.floor((seconds % 86_400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const parts = [
    d ? `${d} day${d === 1 ? "" : "s"}` : "",
    h ? `${h} hour${h === 1 ? "" : "s"}` : "",
    m ? `${m} minute${m === 1 ? "" : "s"}` : "",
  ].filter(Boolean);
  return parts.join(" ");
}

/** The conditions of a rule as short phrases, in reading order. Project and agent come first. */
export function filterWords(
  rule: Pick<AlertRule, "trigger" | "filters" | "project_id">,
  projectName: (id: string) => string,
): string[] {
  const f = rule.filters ?? {};
  const out: string[] = [rule.project_id ? `in ${projectName(rule.project_id)}` : "in any project"];
  if (f.agent) out.push(`by agent ${f.agent}`);
  switch (rule.trigger) {
    case "security_finding":
      if (f.min_severity)
        out.push(`at ${SEVERITY_WORD[f.min_severity] ?? "this"} severity or worse`);
      break;
    case "run_failed": {
      const s = (f.run_statuses?.length ? f.run_statuses : RUN_STATUSES).map(
        (x) => RUN_STATUS_WORD[x as RunStatusFilter] ?? "ended badly",
      );
      out.push(`ending ${s.join(", ")}`);
      break;
    }
    case "spend_over":
      if (f.threshold_usd != null) out.push(`over ${formatCost(f.threshold_usd)} in a UTC day`);
      break;
    case "approval_expiring":
      if (f.expiring_lead_seconds != null)
        out.push(`${durationWords(f.expiring_lead_seconds)} before it expires`);
      break;
    default:
      break;
  }
  return out;
}

export function cooldownWords(seconds: number): string {
  return seconds <= 0
    ? "No cooldown"
    : `At most one alert per destination every ${durationWords(seconds)}`;
}

// ------------------------------------------------------------------------------------------- delivery vocabulary

/** The fixed delivery codes of the API (ADR-151), in words a person can act on. Never a response body. */
const ERROR_CODE_WORD: Record<string, string> = {
  timeout: "The destination did not answer in time.",
  connect_failed: "Could not connect to the destination.",
  dns_failed: "The destination's name could not be looked up.",
  tls_failed: "The secure connection to the destination failed.",
  blocked_address: "The destination points at an address that alerts are not allowed to reach.",
  http_3xx: "The destination answered with a redirect, which is not followed.",
  http_4xx: "The destination refused the alert (a 4xx answer). Check the address or its access.",
  http_5xx: "The destination had an error of its own (a 5xx answer).",
  bad_response: "The destination answered in a way that was not understood.",
  smtp_failed: "The mail server did not accept the alert.",
  config_unreadable: "The saved destination could not be read. Re-create it.",
  internal_error: "Something went wrong on our side while sending.",
};
export const errorCodeWords = (code: string | null | undefined): string =>
  code ? (ERROR_CODE_WORD[code] ?? "Delivery failed for a reason this page does not know.") : "";

const SKIP_WORD: Record<string, string> = {
  cooldown: "Skipped: the rule's cooldown was still running.",
  destination_paused: "Skipped: the destination was paused.",
  destination_deleted: "Skipped: the destination was deleted.",
  rule_deleted: "Skipped: the rule was deleted.",
  rate_limited: "Skipped: too many alerts to this destination in a short time.",
  daily_cap: "Skipped: the destination reached its daily limit.",
};
export const skipWords = (reason: string | null | undefined): string =>
  reason ? (SKIP_WORD[reason] ?? "Skipped.") : "Skipped.";

export type Tone = "ink" | "wait" | "muted";
export type StatusMeta = { glyph: string; word: string; tone: Tone };

/** Status as glyph and word. Amber for trouble; never red (red is a failed run, ADR-111). */
export function notificationStatus(n: Pick<AlertNotification, "status" | "attempts">): StatusMeta {
  switch (n.status) {
    case "delivered":
      return { glyph: "✓", word: "Delivered", tone: "ink" };
    case "pending":
      return n.attempts > 0
        ? { glyph: "↻", word: `Retrying (attempt ${n.attempts + 1})`, tone: "wait" }
        : { glyph: "…", word: "Waiting to send", tone: "muted" };
    case "failed":
      return { glyph: "!", word: "Failed", tone: "wait" };
    case "dead":
      return { glyph: "!", word: "Gave up", tone: "wait" };
    case "skipped":
      return { glyph: "–", word: "Skipped", tone: "muted" };
    default:
      return { glyph: "?", word: "Unknown", tone: "muted" };
  }
}
export const STATUS_FILTERS: readonly NotificationStatus[] = [
  "pending",
  "delivered",
  "failed",
  "dead",
  "skipped",
];
export const STATUS_FILTER_WORD: Record<NotificationStatus, string> = {
  pending: "Waiting or retrying",
  delivered: "Delivered",
  failed: "Failed",
  dead: "Gave up",
  skipped: "Skipped",
};

export function destinationHealth(
  d: Pick<AlertDestination, "state" | "consecutive_failures" | "paused_reason">,
): StatusMeta & { detail: string } {
  if (d.state === "paused") {
    const byFailures = d.paused_reason === "failures";
    return {
      glyph: "‖",
      word: "Paused",
      tone: byFailures ? "wait" : "muted",
      detail: byFailures
        ? `Paused after ${d.consecutive_failures} failures in a row. Resume it once the target works again.`
        : "Paused by a person. Nothing is sent until it is resumed.",
    };
  }
  if (d.state === "failing")
    return {
      glyph: "!",
      word: "Failing",
      tone: "wait",
      detail: `${d.consecutive_failures} failure${d.consecutive_failures === 1 ? "" : "s"} in a row. It pauses itself after 10.`,
    };
  return { glyph: "✓", word: "OK", tone: "ink", detail: "" };
}

export const TYPE_WORD: Record<DestinationType, string> = {
  webhook: "Webhook",
  slack: "Slack",
  email: "Email",
};

// ------------------------------------------------------------------------------------------------------- rule form

export type RuleDraft = {
  name: string;
  trigger: AlertTrigger;
  projectId: string;
  agent: string;
  minSeverity: NonNullable<AlertFilters["min_severity"]>;
  runStatuses: RunStatusFilter[];
  thresholdUsd: string;
  leadMinutes: string;
  destinationIds: string[];
  cooldownMinutes: string;
  enabled: boolean;
};

export const newDraft = (over: Partial<RuleDraft> = {}): RuleDraft => ({
  name: "",
  trigger: "run_failed",
  projectId: "",
  agent: "",
  minSeverity: "high",
  runStatuses: [...RUN_STATUSES],
  thresholdUsd: "",
  leadMinutes: "15",
  destinationIds: [],
  cooldownMinutes: "0",
  enabled: true,
  ...over,
});

export function draftFromRule(r: AlertRule): RuleDraft {
  const f = r.filters ?? {};
  return newDraft({
    name: r.name,
    trigger: r.trigger,
    projectId: r.project_id ?? "",
    agent: f.agent ?? "",
    minSeverity: f.min_severity ?? "high",
    runStatuses: (f.run_statuses?.length ? f.run_statuses : RUN_STATUSES) as RunStatusFilter[],
    thresholdUsd: f.threshold_usd != null ? String(f.threshold_usd) : "",
    leadMinutes: f.expiring_lead_seconds != null ? String(f.expiring_lead_seconds / 60) : "15",
    destinationIds: r.destinations.map((d) => d.id),
    cooldownMinutes: String(Math.round(r.cooldown_seconds / 60)),
    enabled: r.enabled,
  });
}

/** Which filter inputs a trigger needs: the form shows these and nothing else (the API refuses the rest). */
export const FILTERS_FOR: Record<
  AlertTrigger,
  { agent: true; severity?: true; statuses?: true; threshold?: true; lead?: true }
> = {
  run_failed: { agent: true, statuses: true },
  security_finding: { agent: true, severity: true },
  approval_requested: { agent: true },
  approval_expiring: { agent: true, lead: true },
  spend_over: { agent: true, threshold: true },
};

export type DraftResult =
  { ok: true; body: RuleCreate } | { ok: false; errors: Record<string, string> };

/** Builds the request. Only checks what is needed to form a valid request; the API remains the judge of the rest. */
export function buildRuleBody(d: RuleDraft): DraftResult {
  const errors: Record<string, string> = {};
  const name = d.name.trim();
  if (!name) errors.name = "Give the rule a name.";
  if (d.destinationIds.length === 0) errors.destination_ids = "Pick at least one destination.";
  const use = FILTERS_FOR[d.trigger];
  const filters: AlertFilters = {};
  if (d.agent.trim()) filters.agent = d.agent.trim();
  if (use.severity) filters.min_severity = d.minSeverity;
  if (use.statuses) {
    if (d.runStatuses.length === 0) errors.run_statuses = "Pick at least one way a run can end.";
    else filters.run_statuses = d.runStatuses;
  }
  if (use.threshold) {
    const n = Number(d.thresholdUsd);
    if (d.thresholdUsd.trim() === "" || !Number.isFinite(n) || n <= 0)
      errors.threshold_usd = "Enter an amount in dollars above zero.";
    else if (n > 1_000_000) errors.threshold_usd = "The limit is 1,000,000 dollars.";
    else {
      filters.threshold_usd = n;
      filters.period = "day";
    }
  }
  if (use.lead) {
    const m = Number(d.leadMinutes);
    if (d.leadMinutes.trim() === "" || !Number.isFinite(m) || m < 1 || m > MAX_LEAD_MINUTES)
      errors.expiring_lead_seconds = "Enter between 1 and 1440 minutes.";
    else filters.expiring_lead_seconds = Math.round(m * 60);
  }
  const cool = Number(d.cooldownMinutes === "" ? 0 : d.cooldownMinutes);
  if (!Number.isFinite(cool) || cool < 0 || cool > MAX_COOLDOWN_MINUTES)
    errors.cooldown_seconds = "Enter between 0 and 10080 minutes (7 days).";
  if (Object.keys(errors).length) return { ok: false, errors };
  return {
    ok: true,
    body: {
      name,
      trigger: d.trigger,
      project_id: d.projectId || null,
      filters,
      destination_ids: d.destinationIds,
      cooldown_seconds: Math.round(cool * 60),
      enabled: d.enabled,
    },
  };
}

// ------------------------------------------------------------------------------------------------- error mapping

const TARGET_REASON_WORD: Record<string, string> = {
  url_required: "Enter the address.",
  url_too_long: "That address is too long.",
  url_malformed: "That does not look like a web address.",
  not_https: "The address must start with https://.",
  credentials_in_url: "The address must not contain a user name or password.",
  host_invalid: "The host name in that address is not valid.",
  host_internal: "Addresses on an internal network are not allowed.",
  ip_not_public: "That address is not a public one. Alerts only go to public addresses.",
  ip_literal_form: "Write the address with a host name or a standard IP form.",
  port_invalid: "The port in that address is not valid.",
  fragment_not_allowed: "Remove the part after # from the address.",
  recipients_required: "Enter at least one email address.",
  too_many_recipients: "Use at most 10 email addresses.",
  recipient_invalid: "One of the email addresses is not valid.",
  target_shape: "That target does not fit this kind of destination.",
};
const RULE_REASON_WORD: Record<string, string> = {
  not_applicable: "This does not apply to that kind of rule.",
  required: "This is required for that kind of rule.",
  unknown_agent: "No agent with that name was found in this scope.",
  too_small: "That value is too small.",
};
const FILTER_FIELDS = new Set([
  "agent",
  "min_severity",
  "run_statuses",
  "threshold_usd",
  "expiring_lead_seconds",
]);

export type Problem = {
  /** Messages by form field (API field names). */
  fields: Record<string, string>;
  /** A sentence for the banner, when the problem is not about one field. */
  banner: string | null;
};

/** Maps a failed write to field messages and a banner. Server sentences are not shown; the fixed codes are. */
export function problemFrom(error: unknown, kind: "rule" | "destination"): Problem {
  if (!(error instanceof ApiRequestError)) return { fields: {}, banner: null };
  const e = error;
  const d = e.details as { field?: unknown; reason?: unknown };
  switch (e.code) {
    case "ALERT_NAME_TAKEN":
      return {
        fields: {
          name: `Another ${kind} already has this name. Names are compared without regard to case.`,
        },
        banner: null,
      };
    case "ALERT_LIMIT_REACHED":
      return {
        fields: {},
        banner:
          kind === "rule"
            ? "This workspace has reached its limit of 100 alert rules (or this rule has 10 destinations). Delete one first."
            : "This workspace has reached its limit of 50 destinations. Delete one first.",
      };
    case "DESTINATION_TARGET_INVALID": {
      const word = TARGET_REASON_WORD[String(d.reason)] ?? "That target is not valid.";
      return { fields: { target: word }, banner: null };
    }
    case "EMAIL_NOT_CONFIGURED":
      return {
        fields: {},
        banner:
          "Email is not set up on this server. The person who runs Agent Black Box has to configure it first.",
      };
    case "RULE_INVALID": {
      const field = typeof d.field === "string" ? d.field : "";
      const word = RULE_REASON_WORD[String(d.reason)] ?? "That value is not valid.";
      return FILTER_FIELDS.has(field)
        ? { fields: { [field]: word }, banner: null }
        : { fields: {}, banner: word };
    }
    case "PROJECT_NOT_FOUND":
      return { fields: { project_id: "That project was not found." }, banner: null };
    case "ALERT_DESTINATION_NOT_FOUND":
      return {
        fields: {},
        banner: "A destination was deleted in the meantime. Reload the list and try again.",
      };
    case "ALERT_RULE_NOT_FOUND":
      return { fields: {}, banner: "This rule was deleted in the meantime. Reload the list." };
    case "ALERT_BUSY":
      return {
        fields: {},
        banner:
          "Alerts are being sent right now, so this change could not be made. Try again in a moment.",
      };
    case "RATE_LIMITED":
      return {
        fields: {},
        banner: e.retryAfter
          ? `Too many changes in a short time. Try again in ${e.retryAfter} second${e.retryAfter === 1 ? "" : "s"}.`
          : "Too many changes in a short time. Wait a moment and try again.",
      };
    case "PERMISSION_DENIED":
    case "INSUFFICIENT_SCOPE":
      return { fields: {}, banner: "Your role does not allow changing alerts." };
    default: {
      const fields = fieldErrors(e);
      if (Object.keys(fields).length) return { fields, banner: null };
      return { fields: {}, banner: null };
    }
  }
}

export const isDenied = (error: unknown): boolean =>
  error instanceof ApiRequestError && error.status === 403;

/** The preview sentence for a count: "would have fired 3 times in the last 7 days". */
export function previewWords(count: number, days: number): string {
  return `would have fired ${count === 0 ? "no times" : count === 1 ? "once" : `${count} times`} in the last ${days} days`;
}

/** Splits pasted recipients on commas, semicolons, spaces and newlines. Empty pieces are dropped. */
export const parseRecipients = (text: string): string[] =>
  text
    .split(/[\s,;]+/)
    .map((s) => s.trim())
    .filter(Boolean);
