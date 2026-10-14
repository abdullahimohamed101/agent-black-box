"""Alerting vocabulary and value types (ADR-150). Closed sets only: nothing here is free text."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

APPROVAL_REQUESTED: Final = "approval_requested"
APPROVAL_EXPIRING: Final = "approval_expiring"
SECURITY_FINDING: Final = "security_finding"
RUN_FAILED: Final = "run_failed"
SPEND_OVER: Final = "spend_over"
TRIGGERS: Final = (APPROVAL_REQUESTED, APPROVAL_EXPIRING, SECURITY_FINDING, RUN_FAILED, SPEND_OVER)
# Triggers that are checked on a timer rather than when an event is produced.
SCHEDULED_TRIGGERS: Final = (APPROVAL_EXPIRING, SPEND_OVER)

SEVERITY_RANK: Final = {"info": 0, "warning": 1, "medium": 2, "high": 3, "critical": 4}
RUN_FAIL_STATUSES: Final = ("FAILED", "TIMED_OUT", "BLOCKED")

WEBHOOK: Final = "webhook"
SLACK: Final = "slack"
EMAIL: Final = "email"
DESTINATION_TYPES: Final = (WEBHOOK, SLACK, EMAIL)

ACTIVE: Final = "active"
PAUSED: Final = "paused"
FAILING: Final = "failing"

PENDING: Final = "pending"
SKIPPED: Final = "skipped"

# Bounds on rule filters (the API validates; the table's CHECKs are a backstop).
MIN_LEAD_SECONDS: Final = 60
MAX_LEAD_SECONDS: Final = 24 * 3600
MAX_COOLDOWN_SECONDS: Final = 7 * 24 * 3600
MAX_THRESHOLD_USD: Final = 1_000_000.0
MAX_NAME_LENGTH: Final = 80
MAX_PREVIEW_DAYS: Final = 30
DEFAULT_PREVIEW_DAYS: Final = 7


@dataclass(frozen=True)
class DestinationRow:
    """A destination as stored. The ciphertext columns are carried only for the sender and the
    send-test path; no API schema has a field for them."""

    id: uuid.UUID
    name: str
    type: str
    masked_target: str
    state: str
    consecutive_failures: int
    paused_reason: str | None
    key_id: int
    config_encrypted: bytes = field(repr=False)
    signing_secret_encrypted: bytes | None = field(repr=False)
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime
    rule_count: int = 0

    def __repr__(self) -> str:  # never print ciphertext
        return f"DestinationRow(id={self.id}, type={self.type}, state={self.state})"


@dataclass(frozen=True)
class RuleRow:
    id: uuid.UUID
    project_id: uuid.UUID | None
    name: str
    trigger: str
    filters: dict[str, Any]
    cooldown_seconds: int
    enabled: bool
    last_fired_at: datetime | None
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime
    destination_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True)
class RuleTarget:
    """A rule with the destinations (id and state) it sends to, as evaluation needs them."""

    rule: RuleRow
    destinations: tuple[tuple[uuid.UUID, str], ...]


@dataclass(frozen=True)
class NotificationRow:
    id: uuid.UUID
    rule_id: uuid.UUID | None
    rule_name: str | None
    destination_id: uuid.UUID | None
    destination_name: str | None
    destination_type: str | None
    trigger: str
    severity: str
    status: str
    skip_reason: str | None
    attempts: int
    last_error_code: str | None
    project_id: uuid.UUID | None
    run_id: uuid.UUID | None
    finding_id: uuid.UUID | None
    approval_id: uuid.UUID | None
    agent_slug: str | None
    created_at: datetime
    delivered_at: datetime | None
    next_attempt_at: datetime | None


@dataclass(frozen=True)
class Subject:
    """One checkable thing an alert points at, with only the metadata a rule may match on and a
    payload may carry. Built from stored facts, never from event attributes."""

    trigger: str
    dedupe_key: str
    severity: str
    workspace_id: uuid.UUID
    workspace_slug: str
    project_id: uuid.UUID | None
    project_slug: str | None
    project_name: str | None
    agent_slug: str | None
    agent_name: str | None
    run_id: uuid.UUID | None = None
    finding_id: uuid.UUID | None = None
    approval_id: uuid.UUID | None = None
    run_status: str | None = None
    finding_severity: str | None = None
    seconds_to_expiry: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DueNotification:
    """A pending notification claimed for delivery (the stored, content-free payload)."""

    id: uuid.UUID
    rule_id: uuid.UUID | None
    destination_id: uuid.UUID | None
    trigger: str
    payload: dict[str, Any]
    attempts: int
