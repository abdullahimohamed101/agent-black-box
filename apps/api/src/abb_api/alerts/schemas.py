"""HTTP shapes of the alerting API (ADR-150). Nothing here carries a target URL, a recipient
address, a signing secret (but once, on create and rotate) or any captured content."""

from datetime import date, datetime
from typing import Annotated, Literal

from abb_event_schema.ids import IdKind, id_pattern
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SeverityName = Literal["info", "warning", "medium", "high", "critical"]
TriggerName = Literal[
    "approval_requested", "approval_expiring", "security_finding", "run_failed", "spend_over"
]
DestinationTypeName = Literal["webhook", "slack", "email"]
DestinationStateName = Literal["active", "paused", "failing"]
RunFailStatusName = Literal["FAILED", "TIMED_OUT", "BLOCKED"]
NotificationStatusName = Literal["pending", "delivered", "failed", "dead", "skipped"]

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
ProjectIdStr = Annotated[str, Field(pattern=id_pattern(IdKind.PROJECT))]
DestinationIdStr = Annotated[str, Field(pattern=id_pattern(IdKind.DESTINATION))]
AgentSlug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]  # the event contract's


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- destinations


class DestinationTarget(Strict):
    url: str | None = Field(
        default=None,
        max_length=2048,
        description="Webhook and Slack: an https URL. Stored encrypted; never returned.",
    )
    recipients: list[str] | None = Field(
        default=None,
        max_length=10,
        description="Email: 1 to 10 addresses. Stored encrypted; only a masked form is returned.",
    )


class DestinationCreate(Strict):
    name: Name
    type: DestinationTypeName
    target: DestinationTarget


class DestinationPatch(Strict):
    name: Name | None = None
    target: DestinationTarget | None = Field(
        default=None, description="Replaces the target (same shape as at creation)."
    )
    state: Literal["active", "paused"] | None = Field(
        default=None,
        description="`paused` stops delivery; `active` resumes and clears the failure count.",
    )
    rotate_signing_secret: bool = Field(
        default=False, description="Webhook only: issue a new signing secret (returned once)."
    )


class DestinationOut(BaseModel):
    id: str
    name: str
    type: DestinationTypeName
    masked_target: str = Field(
        description="A safe display form computed when the target was saved."
    )
    state: DestinationStateName
    consecutive_failures: int
    paused_reason: Literal["manual", "failures"] | None
    rule_count: int
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


class DestinationTestResult(BaseModel):
    ok: bool = Field(description="True when the destination accepted the test alert.")
    error_code: str | None = Field(
        description=(
            "A fixed code when it did not: `timeout`, `connect_failed`, `dns_failed`, "
            "`tls_failed`, `blocked_address`, `http_3xx`, `http_4xx`, `http_5xx`, `bad_response`, "
            "`smtp_failed`, `config_unreadable`, `internal_error`. Never a response body."
        )
    )


class DestinationCreated(DestinationOut):
    signing_secret: str | None = Field(
        description=(
            "Webhook only: the HMAC signing secret. Shown here once and never again; "
            "rotate it to get a new one."
        )
    )


class DestinationList(BaseModel):
    items: list[DestinationOut]
    email_configured: bool = Field(
        description="Whether the operator configured SMTP; email destinations need it."
    )


# ---------------------------------------------------------------- rules


class RuleFilters(Strict):
    """Per-trigger filters. A field that does not belong to the rule's trigger is a 422."""

    agent: AgentSlug | None = Field(default=None, description="Only this agent (its slug).")
    min_severity: SeverityName | None = Field(
        default=None, description="`security_finding` (required): this severity or worse."
    )
    run_statuses: list[RunFailStatusName] | None = Field(
        default=None, max_length=3, description="`run_failed`: default all three."
    )
    expiring_lead_seconds: int | None = Field(
        default=None,
        ge=60,
        le=86400,
        description="`approval_expiring` (required): alert this long before the approval expires.",
    )
    threshold_usd: float | None = Field(
        default=None,
        gt=0,
        le=1_000_000,
        allow_inf_nan=False,
        description="`spend_over` (required): alert once a UTC day's spend exceeds this.",
    )
    period: Literal["day"] | None = None


class RuleCreate(Strict):
    name: Name
    trigger: TriggerName
    project_id: ProjectIdStr | None = Field(
        default=None, description="Null: every project of the workspace."
    )
    filters: RuleFilters = Field(default_factory=RuleFilters)
    destination_ids: list[DestinationIdStr] = Field(min_length=1, max_length=10)
    cooldown_seconds: int = Field(
        default=0,
        ge=0,
        le=604800,
        description="Per destination: further alerts within this time are recorded as skipped.",
    )
    enabled: bool = True


class RulePatch(Strict):
    """Fields left out stay as they are. The trigger cannot change: make a new rule."""

    name: Name | None = None
    project_id: ProjectIdStr | None = None
    filters: RuleFilters | None = Field(default=None, description="Replaces all filters.")
    destination_ids: list[DestinationIdStr] | None = Field(
        default=None, min_length=1, max_length=10
    )
    cooldown_seconds: int | None = Field(default=None, ge=0, le=604800)
    enabled: bool | None = Field(default=None, description="False mutes the rule.")


class RuleDestinationRef(BaseModel):
    id: str
    name: str
    type: DestinationTypeName
    state: DestinationStateName


class RuleOut(BaseModel):
    id: str
    name: str
    trigger: TriggerName
    project_id: str | None
    filters: RuleFilters
    cooldown_seconds: int
    enabled: bool
    last_fired_at: datetime | None
    destination_count: int
    destinations: list[RuleDestinationRef] = Field(
        description="Owners and admins only; empty for other roles."
    )
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


class RuleList(BaseModel):
    items: list[RuleOut]


class PreviewDay(BaseModel):
    day: date
    count: int


class RulePreview(BaseModel):
    days: int
    count: int = Field(
        description=(
            "How many times the rule would have fired in the window, from recorded metadata, "
            "before cooldown and without delivering anything."
        )
    )
    daily: list[PreviewDay]


# ---------------------------------------------------------------- history


class NotificationRule(BaseModel):
    id: str
    name: str


class NotificationDestination(BaseModel):
    id: str | None = Field(description="Null for roles that may not read destinations.")
    name: str | None = Field(description="Null for roles that may not read destinations.")
    type: DestinationTypeName


class NotificationOut(BaseModel):
    id: str = Field(description="The stable `alert_id` receivers dedupe on.")
    rule: NotificationRule | None = Field(description="Null once the rule was deleted.")
    destination: NotificationDestination | None = Field(
        description="Null once the destination was deleted."
    )
    trigger: TriggerName
    severity: SeverityName
    status: NotificationStatusName
    skip_reason: (
        Literal[
            "cooldown",
            "destination_paused",
            "destination_deleted",
            "rule_deleted",
            "rate_limited",
            "daily_cap",
        ]
        | None
    )
    attempts: int
    error_code: str | None = Field(description="A fixed failure code; never a response body.")
    project_id: str | None
    run_id: str | None
    finding_id: str | None
    approval_id: str | None = Field(description="Null for roles that may not read approvals.")
    agent_slug: str | None
    created_at: datetime
    delivered_at: datetime | None
    next_attempt_at: datetime | None


class NotificationPage(BaseModel):
    items: list[NotificationOut]
    next_cursor: str | None
