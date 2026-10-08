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
