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

# Reserved for later phases (14: policies and approvals, 19: retention). Granted to nobody, so the
# names exist in one place and a future phase only has to add grants.
POLICY_WRITE: Final = "policy.write"
APPROVAL_DECIDE: Final = "approval.decide"
RETENTION_WRITE: Final = "retention.write"

RESERVED_ACTIONS: Final = frozenset({POLICY_WRITE, APPROVAL_DECIDE, RETENTION_WRITE})

ALL_ACTIONS: Final = frozenset(
    {
        WORKSPACE_READ, PROJECT_READ, PROJECT_WRITE, MEMBER_READ, MEMBER_WRITE, MEMBER_WRITE_OWNER,
        INVITE_READ, INVITE_WRITE, RUN_READ, RUN_WRITE, EVENT_WRITE, PAYLOAD_READ, ARTIFACT_READ,
        ARTIFACT_WRITE, ANALYTICS_READ, PRICING_READ, PRICING_WRITE, API_KEY_READ, API_KEY_CREATE,
        API_KEY_REVOKE, AUDIT_READ, BILLING_READ,
    }
) | RESERVED_ACTIONS  # fmt: skip

# Actions that change data. No role holds the ingestion ones: people do not ingest (spec §92).
INGESTION_ACTIONS: Final = frozenset({EVENT_WRITE, RUN_WRITE, ARTIFACT_WRITE})
