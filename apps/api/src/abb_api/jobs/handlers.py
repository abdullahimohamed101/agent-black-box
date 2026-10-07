"""Job handlers. Each must be idempotent (delivery is at-least-once)."""

import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.ingestion.store import PgEventStore
from abb_api.jobs.outbox import SUMMARIZE_RUN, Job
from abb_api.runs.repository import RunRepository
from abb_api.runs.summary import derive_run
from abb_api.tenancy import TenantContext


async def summarize_run(job: Job, conn: AsyncConnection) -> None:
    """Recompute a run's status, summary and spans from all of its stored events."""
    tenant = TenantContext(job.workspace_id)
    run_id = uuid.UUID(job.payload["run_id"])
    runs = RunRepository(conn, tenant)
    if not await runs.lock(run_id):
        return  # the run is gone (retention); nothing to derive
    # Read the events only after the lock is held, so this snapshot includes everything that
    # committed before any job that ran ahead of us on the same run.
    events = await PgEventStore(conn, tenant).load_run(run_id)
    if events:
        await runs.apply_derivation(run_id, derive_run(events))


HANDLERS: dict[str, Callable[[Job, AsyncConnection], Awaitable[None]]] = {
    SUMMARIZE_RUN: summarize_run,
}
