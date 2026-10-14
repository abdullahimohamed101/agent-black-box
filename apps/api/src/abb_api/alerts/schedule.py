"""Scheduling helpers for the delivery and pruning jobs (kept apart so evaluation can arm them
without importing the job handlers)."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.db import tables as t
from abb_api.jobs.outbox import ALERT_PRUNE, DELIVER_ALERTS, OutboxRepository
from abb_api.tenancy import TenantContext


async def request_delivery(outbox: OutboxRepository, tenant: TenantContext) -> bool:
    """Ask for the workspace's due notifications to be sent now (coalesces)."""
    return await outbox.enqueue(
        job_type=DELIVER_ALERTS, dedupe_key=str(tenant.workspace_id), payload={}
    )


async def schedule_retry(conn: AsyncConnection, tenant: TenantContext, at: datetime) -> None:
    """Make sure a retry job exists at or before `at`: an existing pending one is moved earlier,
    never later."""
    statement = insert(t.outbox_jobs).values(
        id=uuid.uuid4(),
        job_type=DELIVER_ALERTS,
        workspace_id=tenant.workspace_id,
        dedupe_key=f"{tenant.workspace_id}:retry",
        payload={},
        available_at=at,
    )
    await conn.execute(
        statement.on_conflict_do_update(
            index_elements=["job_type", "dedupe_key"],
            index_where=text("status = 'pending'"),
            set_={
                "available_at": func.least(
                    t.outbox_jobs.c.available_at, statement.excluded.available_at
                )
            },
        )
    )


async def ensure_prune(
    outbox: OutboxRepository, tenant: TenantContext, delay: timedelta | None = None
) -> bool:
    """Arm the workspace's retention pruning (coalesces). Evaluation arms it with no delay: the
    first run finds nothing old and re-arms itself one interval later while rows remain."""
    return await outbox.enqueue(
        job_type=ALERT_PRUNE, dedupe_key=str(tenant.workspace_id), payload={}, delay=delay
    )
