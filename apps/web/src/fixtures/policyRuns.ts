import { FIXTURE_EPOCH, RunBuilder, type FixtureRun } from "./builder";

/**
 * Two runs that carry policy decisions (Phase 14, builder numbers 20 and 21). Their `policy.*` and `approval.*` events are
 * written the way the server writes them (ADR-141): the emitter name `abb-policy-service`, no `sequence`, only metadata
 * attributes (rule id and version, capability, approval id), never a resource, argument or comment. Both start before every
 * other fixture run so the runs list keeps its order.
 *
 * - WAITING_RUN_N: an agent asked to push to main and waits for a person (the run is WAITING_FOR_APPROVAL).
 * - DECISIONS_RUN_N: an allowed read, a read denied by deny-env-read v3, and a push approved and then used.
 */
export const WAITING_RUN_N = 20;
export const DECISIONS_RUN_N = 21;
const MIN = 60_000;
const POLICY = { name: "abb-policy-service", version: "1.0.0" };
const server = { sdk: POLICY, sequence: null } as const;

const aid = (n: number) => `apr_${String(n).padStart(26, "0")}`;
/** The pending push request of the waiting run. The Approvals fixtures use the same id. */
export const WAITING_APPROVAL_ID = aid(1);
/** The approved-and-used push of the decisions run. */
export const USED_APPROVAL_ID = aid(11);

function open(b: RunBuilder, name: string) {
  b.add("run.started", { "run.name": name }, 0);
  b.add("agent.started", {}, 100, { span_id: b.span(1) });
}

export function waitingForApprovalRun(): FixtureRun {
  const b = new RunBuilder(
    WAITING_RUN_N,
    "Push release to main",
    "release-agent",
    FIXTURE_EPOCH - 400 * MIN,
  );
  open(b, "Push release to main");
  b.add("file.read", { "file.path": "CHANGELOG.md" }, 300);
  b.add(
    "policy.decision",
    {
      "policy.id": "default",
      "policy.version": 3,
      "policy.capability": "filesystem.read",
      "policy.decision": "allow",
    },
    120,
    server,
  );
  b.add(
    "policy.decision",
    {
      "policy.id": "approve-push-main",
      "policy.version": 3,
      "policy.capability": "github.push",
      "policy.decision": "require_approval",
      "policy.reason": "A push to main needs a person to approve it.",
      "approval.id": WAITING_APPROVAL_ID,
    },
    700,
    server,
  );
  b.add(
    "approval.requested",
    {
      "policy.id": "approve-push-main",
      "policy.version": 3,
      "approval.id": WAITING_APPROVAL_ID,
      "approval.state": "REQUESTED",
      "approval.capability": "github.push",
    },
    20,
    server,
  );
  return b.build();
}

export function policyDecisionsRun(): FixtureRun {
  const b = new RunBuilder(
    DECISIONS_RUN_N,
    "Update deploy config",
    "coding-agent",
    FIXTURE_EPOCH - 380 * MIN,
  );
  open(b, "Update deploy config");
  b.add("file.read", { "file.path": "docs/deploy.md" }, 300);
  b.add(
    "policy.decision",
    {
      "policy.id": "default",
      "policy.version": 3,
      "policy.capability": "filesystem.read",
      "policy.decision": "allow",
    },
    120,
    server,
  );
  b.add(
    "policy.action.blocked",
    {
      "policy.id": "deny-env-read",
      "policy.version": 3,
      "policy.capability": "filesystem.read",
      "policy.decision": "deny",
      "policy.reason": "Environment files hold secrets, so agents do not read them.",
    },
    900,
    { ...server, status: "blocked" },
  );
  b.add(
    "policy.decision",
    {
      "policy.id": "approve-push-main",
      "policy.version": 3,
      "policy.capability": "github.push",
      "policy.decision": "require_approval",
      "policy.reason": "A push to main needs a person to approve it.",
      "approval.id": USED_APPROVAL_ID,
    },
    1500,
    server,
  );
  b.add(
    "approval.requested",
    {
      "policy.id": "approve-push-main",
      "policy.version": 3,
      "approval.id": USED_APPROVAL_ID,
      "approval.state": "REQUESTED",
      "approval.capability": "github.push",
    },
    20,
    server,
  );
  b.add(
    "approval.granted",
    {
      "policy.id": "approve-push-main",
      "policy.version": 3,
      "approval.id": USED_APPROVAL_ID,
      "approval.state": "APPROVED",
      "approval.capability": "github.push",
    },
    90_000,
    server,
  );
  b.add(
    "approval.consumed",
    {
      "policy.id": "approve-push-main",
      "policy.version": 3,
      "approval.id": USED_APPROVAL_ID,
      "approval.state": "APPROVED",
      "approval.capability": "github.push",
    },
    800,
    server,
  );
  b.add("git.push", { "git.push_target": "origin main", "git.branch": "main" }, 400);
  b.add("agent.completed", {}, 200, { span_id: b.span(1), status: "success" });
  b.add("run.completed", {}, 100, { status: "success" });
  return b.build();
}

export const policyFixtureRuns = (): FixtureRun[] => [
  waitingForApprovalRun(),
  policyDecisionsRun(),
];
