"""The action vocabulary (spec §91).

Routers and services ask for actions, never for roles or scopes.
"""

from typing import Final

WORKSPACE_READ: Final = "workspace.read"
PROJECT_READ: Final = "project.read"
PROJECT_WRITE: Final = "project.write"
MEMBER_READ: Final = "member.read"
MEMBER_WRITE: Final = "member.write"
MEMBER_WRITE_OWNER: Final = "member.write_owner"
INVITE_READ: Final = "invite.read"
INVITE_WRITE: Final = "invite.write"
RUN_READ: Final = "run.read"
RUN_WRITE: Final = "run.write"
EVENT_WRITE: Final = "event.write"
PAYLOAD_READ: Final = "payload.read"
ARTIFACT_READ: Final = "artifact.read"
ARTIFACT_WRITE: Final = "artifact.write"
ANALYTICS_READ: Final = "analytics.read"
PRICING_READ: Final = "pricing.read"
PRICING_WRITE: Final = "pricing.write"
API_KEY_READ: Final = "api_key.read"
API_KEY_CREATE: Final = "api_key.create"
API_KEY_REVOKE: Final = "api_key.revoke"
AUDIT_READ: Final = "audit.read"
BILLING_READ: Final = "billing.read"

# Evaluations and experiments (Phase 11, ADR-090).
EVALUATION_READ: Final = "evaluation.read"
EVALUATION_RUN: Final = "evaluation.run"
EVALUATION_LABEL: Final = "evaluation.label"
EVALUATOR_WRITE: Final = "evaluator.write"
EXPERIMENT_READ: Final = "experiment.read"
EXPERIMENT_WRITE: Final = "experiment.write"

# Policies (Phase 14, ADR-140). Reading a policy needs `policy.read` (people who build and review
# agents); changing one, validating a draft and the dry run need `policy.write` (owners and
# admins, who may also read payloads: a dry run evaluates recorded content). API keys hold
# neither: management is for signed-in people.
POLICY_READ: Final = "policy.read"
POLICY_WRITE: Final = "policy.write"

# Decisions and approvals (Phase 14 step 2, ADR-141). An agent's key asks (`policy.decide`) and
# uses its own approvals (`approval.use`: wait, consume, withdraw); no person holds either. People
# who can read payloads read and decide approvals (`approval.read`, `approval.decide`; the rule's
# `approver_role` narrows who may decide a given one); no key holds either. `policy.fail_modes`
# is the compact table an SDK caches (keys and the people who read policies).
POLICY_DECIDE: Final = "policy.decide"
APPROVAL_USE: Final = "approval.use"
APPROVAL_READ: Final = "approval.read"
APPROVAL_DECIDE: Final = "approval.decide"
POLICY_FAIL_MODES: Final = "policy.fail_modes"

# Alerting (Phase 17, ADR-150). `alert.read` lets anyone who may read runs see the rules and the
# delivery history (ids, names, trigger, status; never a target or a secret). `alert.write` manages
# rules and destinations and `alert.destination.read` reads destinations (masked target and health):
# owners and admins only. No API key holds any of them: alerting is configured by signed-in people.
ALERT_READ: Final = "alert.read"
ALERT_WRITE: Final = "alert.write"
ALERT_DESTINATION_READ: Final = "alert.destination.read"

# Reserved for later phases (19: retention). Granted to nobody, so the names exist in one place and
# a future phase only has to add grants.
RETENTION_WRITE: Final = "retention.write"

RESERVED_ACTIONS: Final = frozenset({RETENTION_WRITE})

ALL_ACTIONS: Final = frozenset(
    {
        WORKSPACE_READ, PROJECT_READ, PROJECT_WRITE, MEMBER_READ, MEMBER_WRITE, MEMBER_WRITE_OWNER,
        INVITE_READ, INVITE_WRITE, RUN_READ, RUN_WRITE, EVENT_WRITE, PAYLOAD_READ, ARTIFACT_READ,
        ARTIFACT_WRITE, ANALYTICS_READ, PRICING_READ, PRICING_WRITE, API_KEY_READ, API_KEY_CREATE,
        API_KEY_REVOKE, AUDIT_READ, BILLING_READ, EVALUATION_READ, EVALUATION_RUN,
        EVALUATION_LABEL, EVALUATOR_WRITE, EXPERIMENT_READ, EXPERIMENT_WRITE, POLICY_READ,
        POLICY_WRITE, POLICY_DECIDE, APPROVAL_USE, APPROVAL_READ, APPROVAL_DECIDE,
        POLICY_FAIL_MODES, ALERT_READ, ALERT_WRITE, ALERT_DESTINATION_READ,
    }
) | RESERVED_ACTIONS  # fmt: skip

# Actions only an agent's key holds, never a person (spec §92): ingestion, and asking the policy
# service and using its own approvals. A person may still mint a key with these scopes.
INGESTION_ACTIONS: Final = frozenset(
    {EVENT_WRITE, RUN_WRITE, ARTIFACT_WRITE, POLICY_DECIDE, APPROVAL_USE}
)
