import type { FixtureRun } from "./builder";
import { baselineCodingRun, wrongFixCodingRun } from "./codingRuns";
import { withFindings } from "./findings";
import { policyFixtureRuns } from "./policyRuns";
import { securityFixtureRuns } from "./securityRuns";
import { traceStateRuns } from "./traceStateRuns";
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

/** After the trace tier the findings rows are gone: the badges read "not analysed" (ADR-160). */
function withoutFindings(f: FixtureRun): FixtureRun {
  return {
    ...f,
    run: {
      ...f.run,
      findings: { count: 0, max_severity: null, state: "none" },
      security_findings: { count: 0, max_severity: null, state: "none" },
    },
  };
}

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
    ...policyFixtureRuns(),
  ]
    .map(withFindings)
    .sort((a, b) => b.run.started_at.localeCompare(a.run.started_at));
  return cache;
}

let served: FixtureRun[] | undefined;
/**
 * What the fixture API serves: the runs above plus the two whose trace the retention policy removed. They are kept out of
 * `fixtureRuns()` because the analytics, search and replay fixtures (and their tests) are about runs with events.
 */
export function fixtureServedRuns(): FixtureRun[] {
  served ??= [...fixtureRuns(), ...traceStateRuns().map(withoutFindings)].sort((a, b) =>
    b.run.started_at.localeCompare(a.run.started_at),
  );
  return served;
}
