"""Approval value types and the fixed vocabulary of the approvals API (ADR-141)."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

REQUESTED: Final = "REQUESTED"
APPROVED: Final = "APPROVED"
DENIED: Final = "DENIED"
EXPIRED: Final = "EXPIRED"
CANCELLED: Final = "CANCELLED"

# Error codes of consuming an approval (fixed; none echoes any part of the intent).
NOT_FOUND: Final = "APPROVAL_NOT_FOUND"
NOT_APPROVED: Final = "APPROVAL_NOT_APPROVED"
EXPIRED_CODE: Final = "APPROVAL_EXPIRED"
CONSUMED_CODE: Final = "APPROVAL_CONSUMED"
INTENT_MISMATCH: Final = "APPROVAL_INTENT_MISMATCH"
POLICY_DENIED: Final = "APPROVAL_POLICY_DENIED"
NOT_REQUESTED: Final = "APPROVAL_NOT_REQUESTED"


@dataclass(frozen=True)
class Approval:
    """One approval row. `intent_hash` is held for comparison only: no response, event, audit row
    or log line carries it."""

    id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID
    agent_id: str
    capability: str
    resource_scrubbed: str | None
    risk_class: str | None
    arguments_scrubbed: dict[str, Any] | None
    scrub_dropped: bool
    scrub_count: int
    intent_hash: str
    policy_rule_id: str
    policy_version: int
    approver_role: str | None
    allow_self_approval: bool
    on_expiry: str
    requested_at: datetime
    expires_at: datetime
    requested_by_key_id: str
    requested_by_user_id: uuid.UUID | None
    state: str
    decided_by_user_id: uuid.UUID | None
    decided_at: datetime | None
    closed_at: datetime | None
    decision_comment: str | None
    consumed_at: datetime | None
    consumed_run_id: uuid.UUID | None

    def __repr__(self) -> str:  # never print the hash or the scrubbed content in a log or a trace
        return f"Approval(id={self.id}, state={self.state})"

    @property
    def is_open(self) -> bool:
        return self.state == REQUESTED or (self.state == APPROVED and self.consumed_at is None)


@dataclass(frozen=True)
class NewApproval:
    id: uuid.UUID
    project_id: uuid.UUID
    run_id: uuid.UUID
    agent_id: str
    capability: str
    resource_scrubbed: str | None
    risk_class: str | None
    arguments_scrubbed: dict[str, Any] | None
    scrub_dropped: bool
    scrub_count: int
    intent_hash: str
    policy_rule_id: str
    policy_version: int
    approver_role: str | None
    allow_self_approval: bool
    on_expiry: str
    requested_at: datetime
    expires_at: datetime
    requested_by_key_id: str
    requested_by_user_id: uuid.UUID | None
