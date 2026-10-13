import type { FixtureRun } from "./builder";
import { baselineCodingRun, wrongFixCodingRun } from "./codingRuns";
import { withFindings } from "./findings";
import { securityFixtureRuns } from "./securityRuns";
import {
  approvalRun,
  blockedRun,
  cascadeRun,
  expensiveRun,
  failingChainRun,
  failureRetryRun,
  loopingRun,
  multiAgentRun,
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
    multiAgentRun(),
    loopingRun(),
    cascadeRun(),
    failingChainRun(),
    blockedRun(),
    baselineCodingRun(),
    wrongFixCodingRun(),
    ...securityFixtureRuns(),
  ]
    .map(withFindings)
    .sort((a, b) => b.run.started_at.localeCompare(a.run.started_at));
  return cache;
}
