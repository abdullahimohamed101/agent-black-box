"""What every approval transition leaves behind, in the transition's own transaction (ADR-141).

A transition is a single `UPDATE ... WHERE state = ...` (see `store.py`); the caller that won it
then calls one of these, on the same connection, to append the server-written event and the audit
row. A transaction that rolls back therefore takes the event and the audit row with it, and a
transition that lost its race calls nothing, so each transition leaves exactly one event and one
audit row. Nothing here receives or writes the intent, a resource, an argument or a comment.
"""

import logging
import uuid
from datetime import datetime, timedelta

from abb_event_schema.event import Event
from abb_event_schema.ids import IdKind
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.approvals.events import RunRef, transition_event
from abb_api.approvals.model import Approval
from abb_api.approvals.store import PgApprovalStore
from abb_api.audit.repository import ActorKind, AuditEntry, AuditRepository
from abb_api.core.errors import AppError, ErrorCategory
from abb_api.approvals.serverkey import event_id_key
from abb_api.ids import parse_public_id, public_id
from abb_api.ingestion.emitter import SERVER_EMITTER
from abb_api.ingestion.store import PgEventStore
from abb_api.tenancy import TenantContext

logger = logging.getLogger(__name__)
CONFLICT_CODE = "SERVER_EVENT_ID_CONFLICT"
SYSTEM_ACTOR = "system:approval-expiry"
EXPIRY_BATCH = 50


def run_rejected() -> AppError:
    return AppError(
        "RUN_NOT_FOUND", "Run not found.", category=ErrorCategory.NOT_FOUND, status_code=404
    )


def event_record_failed() -> AppError:
    return AppError(
        "EVENT_RECORD_FAILED",
        "The decision could not be recorded; nothing was changed.",
        category=ErrorCategory.INTERNAL,
        status_code=500,
    )


async def emit(
    conn: AsyncConnection, tenant: TenantContext, events: list[Event], summary_delay: timedelta
) -> None:
    """Append server-written events on the caller's transaction (idempotent by event id).

    The transition and its event commit together, so a write that cannot be recorded aborts the
    whole transaction: a decision never takes effect without its record (ADR-141 amendment 2).
    A `conflict` (the id exists with a different body) is acceptable in exactly one case, a retry
    of the server's own write (same emitter, type and approval); anything else means something
    else holds an id only the server should be able to compute, and is refused loudly with a
    fixed code and no content."""
    outcome = await PgEventStore(conn, tenant, summary_delay=summary_delay).ingest(events)
    if outcome.rejected:
        # The only rejection is a run that belongs to another project: callers check first, so
        # this is a race with a hostile client; refuse the whole request.
        raise run_rejected()
    by_id = {e.event_id: e for e in events}
    for result in outcome.results:
        if result.status != "conflict":
            continue
        wanted = by_id[result.event_id]
        if not await _is_server_retry(conn, tenant, wanted):
            logger.error("server event id conflict", extra={"reason": CONFLICT_CODE})
            raise event_record_failed()


async def _is_server_retry(conn: AsyncConnection, tenant: TenantContext, event: Event) -> bool:
    row = (
        await conn.execute(
            text(
                "SELECT event_type, sdk ->> 'name' AS emitter, "
                "attributes ->> 'approval.id' AS approval FROM events "
                "WHERE workspace_id = :w AND event_id = :e"
            ),
            {"w": tenant.workspace_id, "e": parse_public_id(IdKind.EVENT, event.event_id)},
        )
    ).first()
    return (
        row is not None
        and row.emitter == SERVER_EMITTER
        and row.event_type == event.event_type
        and row.approval == event.attributes.get("approval.id")
    )


async def key_of(conn: AsyncConnection) -> bytes:
    return await event_id_key(conn.engine)


async def run_ref(store: PgApprovalStore, approval: Approval) -> RunRef:
    traced = await store.run_trace(approval.run_id)
    trace = traced[1] if traced else approval.run_id  # a run with no events yet: derived trace
    return RunRef(approval.project_id, approval.run_id, trace, approval.agent_id)


async def record(
    conn: AsyncConnection,
    tenant: TenantContext,
    approval: Approval,
    *,
    now: datetime,
    summary_delay: timedelta,
    audit_action: str | None,
    actor_kind: ActorKind,
    actor_id: str,
    consumed: bool = False,
    audit_details: dict[str, object] | None = None,
    current: tuple[str | None, int] | None = None,
) -> None:
    """The event of `approval`'s new state, and (if `audit_action`) its audit row."""
    store = PgApprovalStore(conn, tenant)
    ref = await run_ref(store, approval)
    event = transition_event(
        tenant, ref, approval, now=now, key=await key_of(conn), consumed=consumed, current=current
    )
    await emit(conn, tenant, [event], summary_delay)
    if audit_action is not None:
        await AuditRepository(conn, tenant).append(
            AuditEntry(
                actor_kind=actor_kind,
                actor_id=actor_id,
                action=audit_action,
                resource_kind="approval",
                resource_id=public_id(IdKind.APPROVAL, approval.id),
                details={
                    "approval_id": public_id(IdKind.APPROVAL, approval.id),
                    **(audit_details or {}),
                },
            )
        )


async def record_expiry(
    conn: AsyncConnection,
    tenant: TenantContext,
    approval: Approval,
    *,
    now: datetime,
    summary_delay: timedelta,
) -> None:
    await record(
        conn, tenant, approval, now=now, summary_delay=summary_delay,
        audit_action="approval.expire", actor_kind="system", actor_id=SYSTEM_ACTOR,
        audit_details={"was_approved": approval.decided_by_user_id is not None},
    )  # fmt: skip


async def sweep_expired(
    conn: AsyncConnection,
    tenant: TenantContext,
    now: datetime,
    *,
    summary_delay: timedelta,
    limit: int = EXPIRY_BATCH,
    run_id: uuid.UUID | None = None,
) -> int:
    """Mark open approvals whose time has passed as EXPIRED, each with its event and audit row.
    Idempotent, and safe under concurrency (a row another sweep holds is skipped)."""
    expired = await PgApprovalStore(conn, tenant).expire_due(now, limit=limit, run_id=run_id)
    for approval in expired:
        await record_expiry(conn, tenant, approval, now=now, summary_delay=summary_delay)
    return len(expired)


async def expire_one(
    conn: AsyncConnection,
    tenant: TenantContext,
    approval_id: uuid.UUID,
    now: datetime,
    *,
    summary_delay: timedelta,
) -> bool:
    approval = await PgApprovalStore(conn, tenant).expire(approval_id, now)
    if approval is None:
        return False
    await record_expiry(conn, tenant, approval, now=now, summary_delay=summary_delay)
    return True
