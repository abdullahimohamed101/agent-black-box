"""The `policy.*` and `approval.*` events, written by the server (ADR-141).

A decision is the server's statement, so the server writes the event, in the same transaction as
the state change it describes (the SDK's own `policy.*` events are never trusted for anything).
Events are accepted events (INV-1): new rows only, with ids derived from the approval and the
transition, so a redelivery or a second attempt of the same transition is an idempotent duplicate.
They carry the reserved emitter name (`ingestion.emitter.SERVER_EMITTER`) in `sdk`, which
ingestion refuses from clients, and metadata attributes only: ids, the rule id, a version number,
a closed vocabulary and the policy author's own reason text. No resource, argument, command, path
or comment ever enters an event.

The events have no `sequence`: the server is a second emitter of the run (ADR-070), so a run with
a decision is ordered by time. That is the documented cost of server-written decisions.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from abb_event_schema.event import Event, SdkInfo
from abb_event_schema.ids import IdKind, new_id
from abb_event_schema.versioning import SCHEMA_VERSION

from abb_api import __version__
from abb_api.approvals.model import (
    APPROVED,
    CANCELLED,
    DENIED,
    EXPIRED,
    REQUESTED,
    Approval,
)
from abb_api.ids import public_id
from abb_api.ingestion.emitter import SERVER_EMITTER
from abb_api.tenancy import TenantContext

_EVENT_FOR_STATE = {
    REQUESTED: "approval.requested",
    APPROVED: "approval.granted",
    DENIED: "approval.denied",
    EXPIRED: "approval.expired",
    CANCELLED: "approval.cancelled",
}
CONSUMED = "consumed"
POLICY_DECISION = "policy.decision"
POLICY_BLOCKED = "policy.action.blocked"
MAX_ID_PART = 128


@dataclass(frozen=True)
class RunRef:
    """Where an event goes: enough of a run to address it."""

    project_id: uuid.UUID
    run_id: uuid.UUID
    trace_id: uuid.UUID
    agent_id: str


def derived_event_id(workspace_id: uuid.UUID, *parts: str) -> str:
    """A deterministic `evt_` id: the same inputs always name the same event."""
    material = b"\0".join(p.encode("utf-8") for p in parts)
    digest = hashlib.sha256(b"abb.server-event.v1\0" + workspace_id.bytes + b"\0" + material)
    return public_id(IdKind.EVENT, uuid.UUID(bytes=digest.digest()[:16]))


def _event(
    tenant: TenantContext,
    ref: RunRef,
    *,
    event_id: str,
    event_type: str,
    attributes: dict[str, Any],
    now: datetime,
) -> Event:
    return Event.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "workspace_id": public_id(IdKind.WORKSPACE, tenant.workspace_id),
            "project_id": public_id(IdKind.PROJECT, ref.project_id),
            "run_id": public_id(IdKind.RUN, ref.run_id),
            "trace_id": public_id(IdKind.TRACE, ref.trace_id),
            "agent_id": ref.agent_id,
            "event_type": event_type,
            "occurred_at": now,
            "received_at": now,
            "attributes": attributes,
            "sdk": SdkInfo(name=SERVER_EMITTER, version=__version__),
        }
    )


def decision_event(
    tenant: TenantContext,
    ref: RunRef,
    *,
    kind: str,
    rule_id: str | None,
    policy_version: int,
    capability: str,
    reason: str,
    event_id: str | None,
    now: datetime,
    approval_id: uuid.UUID | None = None,
) -> Event:
    """`policy.decision` (allow, require_approval) or `policy.action.blocked` (deny)."""
    attributes: dict[str, Any] = {
        "policy.id": rule_id or "default",
        "policy.version": policy_version,
        "policy.capability": capability,
        "policy.decision": kind,
        "policy.reason": reason,
    }
    if approval_id is not None:
        attributes["approval.id"] = public_id(IdKind.APPROVAL, approval_id)
    return _event(
        tenant,
        ref,
        event_id=event_id or new_id(IdKind.EVENT),
        event_type=POLICY_BLOCKED if kind == "deny" else POLICY_DECISION,
        attributes=attributes,
        now=now,
    )


def transition_event(
    tenant: TenantContext,
    ref: RunRef,
    approval: Approval,
    *,
    now: datetime,
    consumed: bool = False,
    current: tuple[str | None, int] | None = None,
) -> Event:
    """The event of an approval's new state (or its consumption). `current` is the rule id and
    version of the policy that was re-evaluated when the approval was spent (ADR-141)."""
    extra: dict[str, Any] = {}
    if current is not None:
        extra = {"policy.current_id": current[0] or "default", "policy.current_version": current[1]}
    identifier = public_id(IdKind.APPROVAL, approval.id)
    event_type = "approval.consumed" if consumed else _EVENT_FOR_STATE[approval.state]
    return _event(
        tenant,
        ref,
        event_id=derived_event_id(
            tenant.workspace_id, "approval", identifier, CONSUMED if consumed else approval.state
        ),
        event_type=event_type,
        attributes={
            "approval.id": identifier,
            "approval.state": approval.state,
            "approval.capability": approval.capability,
            "policy.id": approval.policy_rule_id,
            "policy.version": approval.policy_version,
            **extra,
        },
        now=now,
    )
