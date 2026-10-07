export type Base = { workspace: string; project: string };
export const projectPath = (b: Base) => `/w/${b.workspace}/projects/${b.project}`;
export const runsPath = (b: Base) => `${projectPath(b)}/runs`;
export const runPath = (b: Base, runId: string) => `${runsPath(b)}/${runId}`;
/** `all` is the route form of "no project filter" (KI-027: there is no project lookup endpoint yet). */
export const projectFilter = (project: string): string | null =>
  project === "all" ? null : project;
