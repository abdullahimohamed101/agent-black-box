"""The expiry job (ADR-141): close approvals whose time has passed.

Creating an approval enqueues one job due at its `expires_at` (a timer in the outbox, so it
survives restarts and is claimed by any worker). The handler sweeps the whole workspace, so one
firing also closes any other approval that is due, and a missed or late job is harmless: reads
sweep lazily too. Idempotent, as every handler must be: a row that is no longer open is skipped.
"""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.approvals.lifecycle import sweep_expired
from abb_api.clock import system_clock
from abb_api.jobs.context import job_settings
from abb_api.jobs.outbox import Job
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
