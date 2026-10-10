export type Base = { workspace: string; project: string };
export const projectPath = (b: Base) => `/w/${b.workspace}/projects/${b.project}`;
export const runsPath = (b: Base) => `${projectPath(b)}/runs`;
export const analyticsPath = (b: Base) => `${projectPath(b)}/analytics`;
export const runPath = (b: Base, runId: string) => `${runsPath(b)}/${runId}`;
/** `all` is the route form of "no project filter" (a slug or id resolves through `GET /v1/projects`, KI-027). */
export const projectFilter = (project: string): string | null =>
  project === "all" ? null : project;
