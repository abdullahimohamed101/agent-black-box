"""The expiry job (ADR-141): close approvals whose time has passed.

Creating an approval enqueues one job due at its `expires_at` (a timer in the outbox, so it
survives restarts and is claimed by any worker). The handler sweeps the whole workspace, so one
firing also closes any other approval that is due, and a missed or late job is harmless: reads
sweep lazily too. Idempotent, as every handler must be: a row that is no longer open is skipped.
"""

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.approvals.lifecycle import sweep_expired
from abb_api.jobs.context import job_settings
from abb_api.jobs.outbox import Job
from abb_api.tenancy import TenantContext

SWEEP_LIMIT = 200


async def expire_approval(job: Job, conn: AsyncConnection) -> None:
    tenant = TenantContext(job.workspace_id)
    now = (await conn.execute(select(func.now()))).scalar_one()  # the database clock, like leases
    delay = timedelta(seconds=job_settings().summary_debounce_seconds)
    await sweep_expired(conn, tenant, now, summary_delay=delay, limit=SWEEP_LIMIT)
