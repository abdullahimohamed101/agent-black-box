export type Base = { workspace: string; project: string };
export const projectPath = (b: Base) => `/w/${b.workspace}/projects/${b.project}`;
export const runsPath = (b: Base) => `${projectPath(b)}/runs`;
export const analyticsPath = (b: Base) => `${projectPath(b)}/analytics`;
export const runPath = (b: Base, runId: string) => `${runsPath(b)}/${runId}`;
/** `all` is the route form of "no project filter" (a slug or id resolves through `GET /v1/projects`, KI-027). */
export const projectFilter = (project: string): string | null =>
  project === "all" ? null : project;

// Phase 11. The compare page takes both runs in the query string: the left run is the path's, the right one `with`.
export const comparePath = (b: Base, leftRunId: string, rightRunId: string) =>
  `${runsPath(b)}/compare?${new URLSearchParams({ left: leftRunId, right: rightRunId }).toString()}`;
export const experimentsPath = (b: Base) => `${projectPath(b)}/experiments`;
export const experimentPath = (b: Base, experimentId: string) =>
  `${experimentsPath(b)}/${experimentId}`;

// Phase 14. Approvals are a workspace page; the policies editor is a settings page.
export const approvalsPath = (workspace: string) => `/w/${workspace}/approvals`;
export const policiesPath = (workspace: string) => `/w/${workspace}/settings/policies`;

// Phase 17. Alerts are a workspace page; the tab is the URL's `?tab=`.
export const alertsPath = (workspace: string, tab?: "rules" | "destinations" | "history") =>
  `/w/${workspace}/alerts${tab && tab !== "rules" ? `?tab=${tab}` : ""}`;
