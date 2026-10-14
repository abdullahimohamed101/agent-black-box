"""HTTP shapes of the decision and approvals API (ADR-141).

What an agent's key can see of an approval is deliberately small (state, expiry, policy id and
version): no resource, arguments or comment. A signed-in approver sees the scrubbed content they
are asked to approve and never the intent hash.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from abb_api.approvals.intent import IntentIn

DecisionName = Literal["allow", "deny", "require_approval"]
StateName = Literal["REQUESTED", "APPROVED", "DENIED", "EXPIRED", "CANCELLED"]
FailModeName = Literal["open", "closed"]
ExpiryActionName = Literal["deny", "allow"]
RoleName = Literal["OWNER", "ADMIN", "DEVELOPER", "SECURITY"]


class DecideOut(BaseModel):
    decision: DecisionName
    policy_id: str | None = Field(
        description="The id of the rule that decided; null when the policy default applied."
    )
    policy_version: int = Field(description="The policy version that decided (0: none published).")
    reason: str = Field(
        description=(
            "The policy author's own words (a rule's description), never built from the action."
        )
    )
    on_error: FailModeName = Field(
        description=(
            "What the SDK should do the next time the service cannot be reached for an action "
            "like this: the matching rule's `on_error`, else `open` for R0-R2 (or unclassified) "
            "and `closed` for R3-R4."
        )
    )
    approval_id: str | None = Field(description="Set when the decision is `require_approval`.")
    approval_state: StateName | None = None
    expires_at: datetime | None = Field(
        description="When the approval stops being usable (also when approved and unused)."
    )
    on_expiry: ExpiryActionName | None = Field(
        description="The rule's fallback when the approval expires."
    )


class WaitOut(BaseModel):
    approval_id: str
    state: StateName
    expires_at: datetime
    on_expiry: ExpiryActionName
    policy_id: str
    policy_version: int
    consumed: bool = Field(description="True once the approval has been spent.")


class ConsumeIn(BaseModel):
    intent: IntentIn = Field(
        description="The action about to happen, exactly as it was presented to /v1/policy/decide."
    )


class ConsumeOut(BaseModel):
    approval_id: str
    consumed_at: datetime


class ApprovalSummary(BaseModel):
    id: str
    state: StateName
    project_id: str
    run_id: str
    agent_id: str
    capability: str
    resource: str | None = Field(description="Secret-scrubbed; null if it could not be scrubbed.")
    risk_class: str | None
    policy_id: str = Field(description="The rule that required the approval.")
    policy_version: int
    approver_role: RoleName | None = Field(description="Null: an owner or an admin.")
    requested_at: datetime
    expires_at: datetime
    seconds_left: int | None = Field(
        description="Seconds until `expires_at` while the approval is still usable, else null."
    )
    decided_at: datetime | None
    decided_by: str | None = Field(description="The `usr_` id of the person who decided.")
    consumed: bool


class ApprovalDetail(ApprovalSummary):
    arguments: dict[str, Any] | None = Field(
        description="Secret-scrubbed arguments; null if they were withheld."
    )
    content_withheld: bool = Field(
        description="True if the scanner could not clear the resource or arguments."
    )
    redactions: int = Field(description="Secrets replaced in the resource and arguments.")
    allow_self_approval: bool
    on_expiry: ExpiryActionName
    comment: str | None
    closed_at: datetime | None
    consumed_at: datetime | None


class ApprovalList(BaseModel):
    items: list[ApprovalSummary]
    next_cursor: str | None


class PendingCount(BaseModel):
    count: int = Field(description="Requests waiting for a decision right now.")


class DecisionIn(BaseModel):
    decision: Literal["approve", "deny"]
    comment: str | None = Field(default=None, max_length=1000)


class FailModeRule(BaseModel):
    rule_id: str
    capability: str | None = Field(description="Exact or `prefix.*`; null: any capability.")
    resource: str | None = Field(description="Glob source; null: any resource.")
    risk_classes: list[str] = Field(description="Empty: any class.")
    has_attribute_conditions: bool = Field(
        description="The rule also matches on argument attributes, which this table cannot carry."
    )
    on_error: FailModeName


class FailModesOut(BaseModel):
    project_id: str | None
    source: Literal["project", "workspace", "default"]
    policy_version: int
    rules: list[FailModeRule] = Field(
        description="Rules that set `on_error` explicitly, in policy order."
    )
    defaults: dict[str, FailModeName] = Field(
        description="`R0`..`R4` and `unclassified`: the mode when no listed rule applies."
    )
