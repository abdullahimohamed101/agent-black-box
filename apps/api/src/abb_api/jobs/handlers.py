"""Job handlers. Each must be idempotent (delivery is at-least-once)."""

import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.cost.engine import CostEngine
from abb_api.cost.repository import CostRepository
from abb_api.ingestion.store import PgEventStore
from abb_api.jobs.outbox import SUMMARIZE_RUN, Job
from abb_api.runs.repository import RunRepository
from abb_api.runs.summary import derive_run
from abb_api.tenancy import TenantContext

logger = logging.getLogger(__name__)


async def summarize_run(job: Job, conn: AsyncConnection) -> None:
    """Recompute a run's status, summary, spans and cost lines from all of its stored events."""
    tenant = TenantContext(job.workspace_id)
    run_id = uuid.UUID(job.payload["run_id"])
    runs = RunRepository(conn, tenant)
    if not await runs.lock(run_id):
        return  # the run is gone (retention); nothing to derive
    # Read the events only after the lock is held, so this snapshot includes everything that
    # committed before any job that ran ahead of us on the same run.
    events = await PgEventStore(conn, tenant).load_run(run_id)
    if events:
        costs = CostRepository(conn, tenant)
        project_id = await costs.run_project(run_id)
        assert project_id is not None  # the run row is locked, so it exists
        engine = CostEngine(await costs.price_book(project_id))
        derived = derive_run(events, engine)
        skipped = await runs.apply_derivation(run_id, derived)
        await costs.replace_run_lines(run_id, project_id, derived.started_at, derived.cost_lines)
        if skipped:
            logger.warning(
                "spans left untouched: ids already belong to another run",
                extra={"run_id": str(run_id), "skipped": skipped},
            )


HANDLERS: dict[str, Callable[[Job, AsyncConnection], Awaitable[None]]] = {
    SUMMARIZE_RUN: summarize_run,
}
