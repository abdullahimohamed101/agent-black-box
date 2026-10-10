"""Job handlers. Each must be idempotent (delivery is at-least-once)."""

import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.analytics.rollup import refresh_day
from abb_api.cost.engine import CostEngine
from abb_api.cost.repository import CostRepository
from abb_api.findings.job import detect_delay, detect_run
from abb_api.ingestion.store import PgEventStore
from abb_api.jobs.context import job_settings
from abb_api.jobs.outbox import (
    DETECT_FINDINGS,
    REFRESH_ANALYTICS_DAY,
    SUMMARIZE_RUN,
    Job,
    OutboxRepository,
    summarize_key,
)
from abb_api.runs.repository import RunRepository
from abb_api.runs.summary import derive_run
from abb_api.tenancy import TenantContext

logger = logging.getLogger(__name__)

# How long a changed day waits before its analytics rollup is rewritten; changes in between coalesce
# into one refresh (ADR-043). Read from the environment so tests and operators can change it without
# a code path for it.
REFRESH_DELAY_ENV = "ABB_ANALYTICS_REFRESH_DELAY_SECONDS"


def refresh_delay() -> timedelta:
    try:
        return timedelta(seconds=max(float(os.environ.get(REFRESH_DELAY_ENV, "60")), 0.0))
    except ValueError:
        return timedelta(seconds=60)


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
        before = await runs.started_at(run_id)
        engine = CostEngine(await costs.price_book(project_id))
        derived = derive_run(events, engine)
        skipped = await runs.apply_derivation(run_id, derived, project_id)
        await costs.replace_run_lines(run_id, project_id, derived.started_at, derived.cost_lines)
        # Both the day the run was in and the day it is in now need their rollup rewritten.
        outbox = OutboxRepository(conn, tenant)
        for day in {derived.started_at.date(), *([before.date()] if before else [])}:
            await outbox.enqueue(
                job_type=REFRESH_ANALYTICS_DAY,
                dedupe_key=f"{tenant.workspace_id}:{day.isoformat()}",
                payload={"day": day.isoformat()},
                delay=refresh_delay(),
            )
        if skipped:
            logger.warning(
                "spans left untouched: ids already belong to another run",
                extra={"run_id": str(run_id), "skipped": skipped},
            )
        await _enqueue_detect(outbox, tenant, run_id, len(events))


async def _enqueue_detect(
    outbox: OutboxRepository, tenant: TenantContext, run_id: uuid.UUID, event_count: int
) -> None:
    """Ask for the run's findings. Always, even when the event count is unchanged: a cost rebuild
    changes an input of the cost-spike detector (ADR-080 (6)). Coalesces per run."""
    await outbox.enqueue(
        job_type=DETECT_FINDINGS,
        dedupe_key=summarize_key(tenant.workspace_id, run_id),
        payload={"run_id": str(run_id)},
        delay=detect_delay(job_settings().findings_delay_seconds, event_count),
    )


async def refresh_analytics_day(job: Job, conn: AsyncConnection) -> None:
    """Rewrite one workspace-day of analytics rollups from the derived tables (idempotent)."""
    await refresh_day(conn, TenantContext(job.workspace_id), date.fromisoformat(job.payload["day"]))


async def detect_findings(job: Job, conn: AsyncConnection) -> None:
    """Recompute a run's findings from its events (idempotent, one writer per run)."""
    settings = job_settings()
    tenant = TenantContext(job.workspace_id)
    run_id = uuid.UUID(job.payload["run_id"])
    outcome = await detect_run(conn, tenant, run_id, settings)
    if outcome == "busy":
        # Another worker is writing this run's findings. Do not hold a worker slot waiting: come
        # back later (coalesced), when its result is in.
        await OutboxRepository(conn, tenant).enqueue(
            job_type=DETECT_FINDINGS,
            dedupe_key=job.dedupe_key,
            payload=job.payload,
            delay=timedelta(seconds=max(settings.findings_delay_seconds, 1.0)),
        )


HANDLERS: dict[str, Callable[[Job, AsyncConnection], Awaitable[None]]] = {
    SUMMARIZE_RUN: summarize_run,
    REFRESH_ANALYTICS_DAY: refresh_analytics_day,
    DETECT_FINDINGS: detect_findings,
}
