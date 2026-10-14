"""The expiry job (ADR-141): close approvals whose time has passed.

Creating an approval enqueues one job due at its `expires_at` (a timer in the outbox, so it
survives restarts and is claimed by any worker). The handler sweeps the whole workspace, so one
firing also closes any other approval that is due, and a missed or late job is harmless: reads
sweep lazily too. Idempotent, as every handler must be: a row that is no longer open is skipped.
"""

import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.approvals.lifecycle import sweep_expired
from abb_api.approvals.store import PgApprovalStore
from abb_api.clock import system_clock
from abb_api.jobs.context import job_settings
from abb_api.jobs.outbox import EXPIRE_APPROVAL, Job, OutboxRepository
from abb_api.tenancy import TenantContext

SWEEP_LIMIT = 200


async def expire_approval(job: Job, conn: AsyncConnection) -> None:
    tenant = TenantContext(job.workspace_id)
    # The application clock, like every other approval time (ADR-141 point 4). The queue schedules
    # the job by the database clock, but what the job STAMPS must agree with `expires_at`, the
    # API's lazy sweeps and the agent's own events: a database clock ahead of the host's (a VM, a
    # container) closed approvals early and stamped `approval.expired` after the run's own end,
    # which left that run RUNNING (found by `policy-e2e`).
    now = system_clock()
    delay = timedelta(seconds=job_settings().summary_debounce_seconds)
    await sweep_expired(conn, tenant, now, summary_delay=delay, limit=SWEEP_LIMIT)
    # The queue fires by the database clock, which may run ahead of the application's: if this
    # approval is still open and not yet due by OUR clock, the job came early. Re-arm it for the
    # moment it is due, or its `approval.expired` and audit row would wait for someone to read
    # (review P3-2). A one-second margin keeps a rounding difference from looping.
    raw = job.payload.get("approval_id") if isinstance(job.payload, dict) else None
    try:
        approval_id = uuid.UUID(str(raw))
    except ValueError:
        return
    approval = await PgApprovalStore(conn, tenant).get(approval_id)
    if approval is not None and approval.is_open and approval.expires_at > now:
        await OutboxRepository(conn, tenant).enqueue(
            job_type=EXPIRE_APPROVAL,
            dedupe_key=str(approval.id),
            payload={"approval_id": str(approval.id)},
            delay=approval.expires_at - now + timedelta(seconds=1),
        )
