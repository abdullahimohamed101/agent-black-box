"""HTTP shapes of the policy API (ADR-140). Policy documents are author text and are returned to
the roles that may read policies; the dry run returns counts, rule ids and ids only, never any
captured content."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from abb_api.policies.model import MAX_DOCUMENT_BYTES

ScopeName = Literal["workspace", "project"]
SourceName = Literal["project", "workspace", "default"]
DecisionName = Literal["allow", "deny", "require_approval"]
# A character cap well above the byte cap: the parser reports "too large" in bytes, with a code.
MAX_DOCUMENT_CHARS = 4 * MAX_DOCUMENT_BYTES


class IssueOut(BaseModel):
    line: int = Field(description="1-based line of the offending entry.")
    column: int = Field(description="1-based column.")
    code: str = Field(description="Stable code such as `unknown_key` or `invalid_duration`.")
    message: str = Field(description="A fixed sentence. It may name a key, never a value.")


class ValidateIn(BaseModel):
    document: str = Field(max_length=MAX_DOCUMENT_CHARS, description="The policy YAML.")


class ValidateOut(BaseModel):
    valid: bool
    issues: list[IssueOut]
    rule_count: int | None = Field(description="Rules in the document when it is valid.")


class PolicyVersionOut(BaseModel):
    scope: ScopeName
    project_id: str | None = Field(description="Null for the workspace-wide base policy.")
    version: int = Field(description="1, 2, 3 ... per scope; never reused.")
    document_sha256: str
    rule_count: int
    created_at: datetime
    created_by: str
    comment: str | None


class PolicyVersionDetail(PolicyVersionOut):
    document: str = Field(description="The policy text exactly as published.")


class CurrentPolicyOut(BaseModel):
    project_id: str | None = Field(description="The project asked about; null for the base.")
    source: SourceName = Field(
        description=(
            "Where the governing policy comes from: the project's own, the workspace base, or "
            "`default` (nothing published: every action is allowed)."
        )
    )
    policy: PolicyVersionDetail | None = Field(description="Null when `source` is `default`.")


class PolicyHistory(BaseModel):
    items: list[PolicyVersionOut]
    next_before_version: int | None = Field(
        description="Pass as `before_version` for the next (older) page."
    )


class PublishIn(BaseModel):
    document: str = Field(max_length=MAX_DOCUMENT_CHARS)
    project_id: str | None = Field(
        default=None, description="The project to publish for; omit for the workspace base."
    )
    comment: str | None = Field(default=None, max_length=500)
    base_version: int | None = Field(
        default=None,
        ge=0,
        description=(
            "The version the editor started from (0: none yet). If the scope's current version "
            "differs the request is a 409 and nothing is stored."
        ),
    )


class DiffOut(BaseModel):
    project_id: str | None
    from_version: int
    to_version: int
    diff: str = Field(description="Unified diff text, at most 64 KiB.")
    truncated: bool


class DryRunIn(BaseModel):
    project_id: str = Field(description="The project whose recorded events are replayed.")
    document: str = Field(max_length=MAX_DOCUMENT_CHARS, description="The draft policy.")


class DryRunRuleCount(BaseModel):
    rule_id: str | None = Field(description="Null: no rule matched and the default applied.")
    decision: DecisionName
    count: int


class DryRunSample(BaseModel):
    """An id-only pointer to one recorded action the draft would not simply allow."""

    run_id: str
    event_id: str
    capability: str
    risk_class: str | None
    decision: DecisionName
    rule_id: str | None


class DryRunLimitsOut(BaseModel):
    max_runs: int
    max_events: int
    time_budget_seconds: float
    max_samples: int


class DryRunOut(BaseModel):
    runs_considered: int
    events_evaluated: int = Field(description="Recorded actions the draft was evaluated against.")
    events_skipped: int = Field(description="Events read that are not guardable actions.")
    truncated: Literal["runs", "events", "time_budget"] | None = Field(
        description="Why the replay stopped early, if it did; counts then cover a prefix."
    )
    by_decision: dict[str, int]
    by_rule: list[DryRunRuleCount]
    samples: list[DryRunSample]
    limits: DryRunLimitsOut
