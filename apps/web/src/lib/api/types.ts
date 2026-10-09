import type { components, paths } from "./schema";

export type { paths };
export type RunOut = components["schemas"]["RunOut"];
export type RunPage = components["schemas"]["RunPage"];
export type EventOut = components["schemas"]["EventOut"];
export type EventPage = components["schemas"]["EventPage"];
export type SpanOut = components["schemas"]["SpanOut"];
export type SpanPage = components["schemas"]["SpanPage"];
export type ArtifactChunk = components["schemas"]["ArtifactChunk"];
export type RunStatus = RunOut["status"];
export type AnalyticsSummary = components["schemas"]["Summary"];
export type CostReport = components["schemas"]["CostReport"];
export type ReliabilityReport = components["schemas"]["ReliabilityReport"];
export type PerformanceReport = components["schemas"]["PerformanceReport"];

export const ALL_STATUSES: readonly RunStatus[] = [
  "QUEUED",
  "RUNNING",
  "WAITING",
  "WAITING_FOR_APPROVAL",
  "SUCCESS",
  "FAILED",
  "CANCELLED",
  "TIMED_OUT",
  "BLOCKED",
];
/** Statuses a run can still leave; everything else is final (api-v1.md state machine). */
export const ACTIVE_STATUSES: readonly RunStatus[] = [
  "QUEUED",
  "RUNNING",
  "WAITING",
  "WAITING_FOR_APPROVAL",
];
export const isActive = (s: RunStatus): boolean => ACTIVE_STATUSES.includes(s);
export type ExpensiveRun = components["schemas"]["ExpensiveRun"] & { name: string | null };

export type MeOut = components["schemas"]["MeOut"];
export type ProjectOut = components["schemas"]["ProjectOut"];
export type MemberOut = components["schemas"]["MemberOut"];
export type InvitationOut = components["schemas"]["InvitationOut"];
export type ApiKeyOut = components["schemas"]["ApiKeyOut"];
export type PriceOut = components["schemas"]["PriceOut"];
export type AuditEntryOut = components["schemas"]["AuditEntryOut"];
export type Role = MemberOut["role"];
export type KeyScope = ApiKeyOut["scopes"][number];

// The vocabularies of the API contract (OpenAPI enums), for form controls. Not a rights matrix.
export const ROLES = [
  "OWNER",
  "ADMIN",
  "DEVELOPER",
  "VIEWER",
  "SECURITY",
  "BILLING",
] as const satisfies readonly Role[];
export const KEY_SCOPES = [
  "runs:read",
  "events:write",
  "artifacts:write",
  "policy:check",
] as const satisfies readonly KeyScope[];
