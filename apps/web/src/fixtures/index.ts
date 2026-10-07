import type { FixtureRun } from "./builder";
import {
  approvalRun,
  expensiveRun,
  failureRetryRun,
  runningRun,
  stressRun,
  successRun,
} from "./runs";

export { FIXTURE_PROJECT } from "./builder";

let cache: FixtureRun[] | undefined;
/** Newest first, like the API's default sort. Built lazily once per process. */
export function fixtureRuns(): FixtureRun[] {
  cache ??= [
    successRun(),
    failureRetryRun(),
    expensiveRun(),
    runningRun(),
    approvalRun(),
    stressRun(),
  ].sort((a, b) => b.run.started_at.localeCompare(a.run.started_at));
  return cache;
}
