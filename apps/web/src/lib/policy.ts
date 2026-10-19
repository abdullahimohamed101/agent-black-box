import type { components } from "./api/schema";
import type { EventOut } from "./api/types";

/**
 * Presentation logic for policies and approvals (Phase 14, ADR-140, ADR-141). Everything here reads known fields only:
 * the API sends scrubbed intents and fixed codes, and the readers below ignore every field they do not name. Colour never
 * means a verdict here (a denial is a policy outcome, not a failed run, ADR-111): outcomes are a glyph, a word and weight.
 */

export type ApprovalSummary = components["schemas"]["ApprovalSummary"];
export type ApprovalDetail = components["schemas"]["ApprovalDetail"];
export type ApprovalState = ApprovalSummary["state"];
export type PolicyVersionDetail = components["schemas"]["PolicyVersionDetail"];
export type PolicyVersionOut = components["schemas"]["PolicyVersionOut"];
export type PolicyIssue = components["schemas"]["abb_api__policies__schemas__IssueOut"];
export type DryRunOut = components["schemas"]["DryRunOut"];
export type PolicyDecisionWord = "allow" | "deny" | "require_approval";

/** The one sentence that states the guarantee (D1). It is shown, never softened, on both pages. */
export const COOPERATIVE_NOTE =
  "Policies are enforced for agents that ask Agent BlackBox through the SDK guard. They do not stop code that bypasses it.";

/** The name server-written policy and approval events carry as `sdk.name` (ADR-141); the server rejects it from clients. */
export const POLICY_SERVICE = "abb-policy-service";
export const PROVENANCE_TEXT = "Recorded by the policy service";
export const isPolicyService = (e: Pick<EventOut, "sdk">): boolean =>
  e.sdk != null && (e.sdk as Record<string, unknown>)["name"] === POLICY_SERVICE;

/** The starter policy of spec section 151, valid under ADR-140. Inserted by "Start from the example". */
export const STARTER_POLICY = `# Rules are checked in order and the first match wins. Anything that matches no rule is allowed.
# Enforcement is cooperative: it applies to agents that ask through the SDK guard.
version: 1
policies:
  - id: deny-env-read
    description: Environment files hold secrets, so agents do not read them.
    when:
      capability: filesystem.read
      resource: "**/.env*"
    decision: deny

  - id: approve-push-main
    description: A push to main needs a person to approve it.
    when:
      capability: github.push
      attributes:
        branch: main
    decision: require_approval
    approver_role: admin
    expires_in: 15m

  - id: deny-catastrophic-shell
    description: Catastrophic commands are never run by an agent.
    when:
      capability: shell.execute
      risk_class: R4
    decision: deny
`;

export const MAX_COMMENT_CHARS = 1000;
export const MAX_PUBLISH_COMMENT_CHARS = 500;

// ---- approvals ------------------------------------------------------------------------------------------------

type Tone = "ink" | "wait" | "muted";
export type StateMeta = { glyph: string; word: string; tone: Tone };

/** Approval state as glyph and word. Waiting and expired are amber; a denial is ink weight, never red. */
export function approvalStateMeta(a: Pick<ApprovalSummary, "state" | "consumed">): StateMeta {
  switch (a.state) {
    case "REQUESTED":
      return { glyph: "❙❙", word: "Waiting", tone: "wait" };
    case "APPROVED":
      return a.consumed
        ? { glyph: "✓", word: "Approved, used", tone: "ink" }
        : { glyph: "✓", word: "Approved", tone: "ink" };
    case "DENIED":
      return { glyph: "⊘", word: "Denied", tone: "ink" };
    case "EXPIRED":
      return { glyph: "◌", word: "Expired", tone: "wait" };
    case "CANCELLED":
      return { glyph: "–", word: "Cancelled", tone: "muted" };
    default:
      return { glyph: "?", word: String(a.state).slice(0, 24), tone: "muted" };
  }
}

/** Risk class as a shield mark (filled for R4, amber outline for R3, muted otherwise) and the word "Risk R3". */
export function riskMeta(risk: string | null | undefined): {
  word: string;
  filled: boolean;
  tone: Tone;
} {
  switch (risk) {
    case "R4":
      return { word: "Risk R4", filled: true, tone: "ink" };
    case "R3":
      return { word: "Risk R3", filled: false, tone: "wait" };
    case "R0":
    case "R1":
    case "R2":
      return { word: `Risk ${risk}`, filled: false, tone: "muted" };
    default:
      return { word: "Unclassified", filled: false, tone: "muted" };
  }
}

export const ROLE_WORD: Record<string, string> = {
  OWNER: "Owner",
  ADMIN: "Admin",
  DEVELOPER: "Developer",
  SECURITY: "Security",
};
/** Who may decide, in words: an unset role means an owner or an admin (ADR-141). */
export const approverText = (role: ApprovalSummary["approver_role"]): string =>
  role ? `${ROLE_WORD[role] ?? role} or above` : "Owner or admin";

/**
 * Time left, never negative. `leftSeconds` is the server's `seconds_left` aged by the time since it was read, so a
 * wrong clock on this machine cannot change it. Null means the question does not apply (a decided request).
 */
export function leftText(leftSeconds: number | null): string | null {
  if (leftSeconds == null) return null;
  const s = Math.max(0, Math.floor(leftSeconds));
  if (s <= 0) return "Expired";
  if (s < 60) return `${s} s left`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min left`;
  const h = Math.floor(m / 60);
  return m % 60 === 0 ? `${h} h left` : `${h} h ${m % 60} min left`;
}

/** Seconds left now: the server's figure minus the time since it arrived; floored at zero, never negative. */
export const ageSeconds = (
  secondsLeft: number | null,
  receivedAt: number,
  now: number,
): number | null =>
  secondsLeft == null ? null : Math.max(0, secondsLeft - Math.max(0, now - receivedAt) / 1000);

export type ApprovalTab = "pending" | "decided" | "all";
export const APPROVAL_TABS: readonly { key: ApprovalTab; label: string }[] = [
  { key: "pending", label: "Pending" },
  { key: "decided", label: "Decided" },
  { key: "all", label: "All" },
];
export const parseApprovalTab = (v: string | null | undefined): ApprovalTab =>
  v === "decided" || v === "all" ? v : "pending";

/** The API's fixed error codes for a decision, in words. Nothing from the request or the response body is echoed. */
export function decisionErrorText(code: string, status: number, state?: unknown): string {
  switch (code) {
    case "APPROVER_ROLE_REQUIRED":
      return "Your role cannot decide this request.";
    case "SELF_APPROVAL_FORBIDDEN":
      return "You cannot decide a request made with your own API key.";
    case "APPROVAL_NOT_REQUESTED":
      return `Someone else decided first${typeof state === "string" ? `: it is now ${state.toLowerCase()}` : ""}.`;
    case "APPROVAL_CONTENT_UNAVAILABLE":
      return "The content of this request could not be shown, so it cannot be approved. You can deny it.";
    case "APPROVAL_NOT_FOUND":
      return "This approval was not found.";
    default:
      if (status === 404) return "This approval was not found.";
      if (status === 403) return "Your role cannot decide this request.";
      if (status === 429) return "Too many requests. Try again in a moment.";
      return "The decision could not be saved. Nothing was changed.";
  }
}

// ---- policy events on the run page -----------------------------------------------------------------------------

const attrText = (e: EventOut, key: string): string | null => {
  const v = e.attributes[key];
  return typeof v === "string" && v ? v : null;
};

export type PolicyEventLabel = StateMeta & {
  /** The author-written reason (`policy.reason`), for a denial; policy text, never captured content. */
  detail: string | null;
};

/** `rule v3`, `rule` when the version is unknown, null when no rule matched (the policy default applied). */
function ruleText(e: EventOut): string | null {
  const id = attrText(e, "policy.id");
  if (!id || id === "default") return null;
  const v = e.attributes["policy.version"];
  return typeof v === "number" && Number.isInteger(v) ? `${id} v${v}` : id;
}

export const POLICY_EVENT_TYPES: ReadonlySet<string> = new Set([
  "policy.decision",
  "policy.action.blocked",
  "approval.requested",
  "approval.granted",
  "approval.denied",
  "approval.expired",
  "approval.cancelled",
  "approval.consumed",
]);

/**
 * The glyph and words for a policy or approval event: "Allowed by deny-x v3", "Denied by deny-env-read v3", "Approval
 * requested", "Approved", "Approval used". Null for every other event type. A denial here is a policy outcome and
 * never an error (red is for a failed run).
 */
export function policyEventLabel(e: EventOut): PolicyEventLabel | null {
  const rule = ruleText(e);
  switch (e.event_type) {
    case "policy.decision": {
      const decision = attrText(e, "policy.decision");
      if (decision === "require_approval")
        return {
          glyph: "❙❙",
          word: rule ? `Approval required by ${rule}` : "Approval required (default)",
          tone: "wait",
          detail: attrText(e, "policy.reason"),
        };
      if (decision === "deny")
        return {
          glyph: "⊘",
          word: rule ? `Denied by ${rule}` : "Denied (default)",
          tone: "ink",
          detail: attrText(e, "policy.reason"),
        };
      return {
        glyph: "✓",
        word: rule ? `Allowed by ${rule}` : "Allowed (default)",
        tone: "ink",
        detail: null,
      };
    }
    case "policy.action.blocked":
      return {
        glyph: "⊘",
        word: rule ? `Denied by ${rule}` : "Denied (default)",
        tone: "ink",
        detail: attrText(e, "policy.reason"),
      };
    case "approval.requested":
      return { glyph: "❙❙", word: "Approval requested", tone: "wait", detail: null };
    case "approval.granted":
      return { glyph: "✓", word: "Approved", tone: "ink", detail: null };
    case "approval.denied":
      return { glyph: "⊘", word: "Denied", tone: "ink", detail: null };
    case "approval.expired":
      return { glyph: "◌", word: "Approval expired", tone: "wait", detail: null };
    case "approval.cancelled":
      return { glyph: "–", word: "Approval cancelled", tone: "muted", detail: null };
    case "approval.consumed":
      return { glyph: "✓", word: "Approval used", tone: "ink", detail: null };
    default:
      return null;
  }
}

/** What the run is waiting on: the newest `approval.requested`'s id and capability, when no later event closed it. */
export function pendingApproval(
  events: readonly EventOut[],
): { id: string | null; capability: string | null } | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i]!;
    if (e.event_type === "approval.requested")
      return { id: attrText(e, "approval.id"), capability: attrText(e, "approval.capability") };
    if (
      e.event_type === "approval.granted" ||
      e.event_type === "approval.denied" ||
      e.event_type === "approval.expired" ||
      e.event_type === "approval.cancelled"
    )
      return null;
  }
  return null;
}

// ---- diff -------------------------------------------------------------------------------------------------------

export type DiffLine = { kind: "add" | "del" | "hunk" | "head" | "ctx"; text: string };

/** Classifies a unified diff line by line. Added lines are ink, removed lines amber (not red), headers muted. */
export function diffLines(diff: string): DiffLine[] {
  if (!diff) return [];
  return diff
    .replace(/\n$/, "")
    .split("\n")
    .map((text): DiffLine => {
      if (text.startsWith("+++") || text.startsWith("---")) return { kind: "head", text };
      if (text.startsWith("@@")) return { kind: "hunk", text };
      if (text.startsWith("+")) return { kind: "add", text };
      if (text.startsWith("-")) return { kind: "del", text };
      return { kind: "ctx", text };
    });
}

// ---- editor -----------------------------------------------------------------------------------------------------

/** The offset of line:column (both 1-based) in the text; clamped to the text. */
export function offsetOf(text: string, line: number, column: number): number {
  let at = 0;
  for (let i = 1; i < line; i++) {
    const nl = text.indexOf("\n", at);
    if (nl < 0) return text.length;
    at = nl + 1;
  }
  const end = text.indexOf("\n", at);
  const lineEnd = end < 0 ? text.length : end;
  return Math.min(at + Math.max(0, column - 1), lineEnd);
}

export const lineCount = (text: string): number => text.split("\n").length;

export function sourceText(source: "project" | "workspace" | "default", scopeIsBase: boolean) {
  if (source === "project") return "this project's own policy";
  if (source === "workspace")
    return scopeIsBase ? "the workspace policy" : "inherited from the workspace policy";
  return "no policy published yet; everything is allowed";
}

/** What a dry run says a draft would do, as glyph and word (it did not do anything). */
export function dryDecisionMeta(d: string): StateMeta {
  switch (d) {
    case "allow":
      return { glyph: "✓", word: "Would allow", tone: "ink" };
    case "deny":
      return { glyph: "⊘", word: "Would deny", tone: "ink" };
    case "require_approval":
      return { glyph: "❙❙", word: "Would need approval", tone: "wait" };
    default:
      return { glyph: "?", word: String(d).slice(0, 24), tone: "muted" };
  }
}

export const TRUNCATED_TEXT: Record<string, string> = {
  runs: "It stopped at its limit of runs, so older runs were not checked.",
  events: "It stopped at its limit of events, so some events were not checked.",
  time_budget: "It stopped at its time limit, so some events were not checked.",
};

/** The web proxy caps a request body at 64 KiB; a document that would not fit says so instead of failing late. */
export const MAX_WEB_DOCUMENT_BYTES = 60_000;
export const tooLargeForWeb = (text: string): boolean =>
  new TextEncoder().encode(JSON.stringify({ document: text })).length > MAX_WEB_DOCUMENT_BYTES;
