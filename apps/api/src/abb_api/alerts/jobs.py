"""The alerting jobs on the outbox (ADR-150).

`evaluate_alerts` is enqueued by the code that already produces the fact (a run summarised into a
failed status, a security finding saved or raised, an approval requested) in the producer's own
transaction, keyed by (trigger, subject id): the producer pays one small INSERT and never evaluates
anything inline. The handler matches the fact against the workspace's rules.

`alert_sweep` is the timer for the two scheduled triggers (approval close to expiring, daily
spend). It is one job per workspace that re-enqueues itself while the workspace has an enabled
scheduled rule, and is started by the API whenever such a rule is created, edited or switched on.
The chain ends by itself when the last scheduled rule is switched off. Several worker processes are
safe: the queue hands a job to one worker, and every write is idempotent on the notification key.
"""

import logging
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.alerts import model as m
from abb_api.alerts.evaluate import evaluate_event, sweep
from abb_api.alerts.repository import RuleRepository
from abb_api.clock import system_clock
from abb_api.jobs.context import job_settings
from abb_api.jobs.outbox import ALERT_SWEEP, EVALUATE_ALERTS, Job, OutboxRepository
from abb_api.tenancy import TenantContext

logger = logging.getLogger(__name__)

EVENT_TRIGGERS = (m.APPROVAL_REQUESTED, m.SECURITY_FINDING, m.RUN_FAILED)


async def enqueue_event(outbox: OutboxRepository, trigger: str, subject_id: uuid.UUID) -> bool:
    """Ask for one fact to be matched against the alert rules (coalesces per trigger/subject)."""
    return await outbox.enqueue(
        job_type=EVALUATE_ALERTS,
        dedupe_key=f"{trigger}:{subject_id}",
        payload={"trigger": trigger, "subject_id": str(subject_id)},
    )


async def ensure_sweep(outbox: OutboxRepository, tenant: TenantContext) -> bool:
    """Start (or keep) the workspace's scheduled-check timer."""
    return await outbox.enqueue(
        job_type=ALERT_SWEEP, dedupe_key=str(tenant.workspace_id), payload={}
    )


async def evaluate_alerts(job: Job, conn: AsyncConnection) -> None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    trigger = payload.get("trigger")
    try:
        subject_id = uuid.UUID(str(payload.get("subject_id")))
    except ValueError:
        logger.warning("alert job with a malformed subject", extra={"job_id": str(job.id)})
        return
    if trigger not in EVENT_TRIGGERS:
        logger.warning("alert job with an unknown trigger", extra={"job_id": str(job.id)})
        return
    settings = job_settings()
    await evaluate_event(
        conn,
        TenantContext(job.workspace_id),
        str(trigger),
        subject_id,
        now=system_clock(),
        skipped_cap=settings.alerts_skipped_per_window,
    )


async def alert_sweep(job: Job, conn: AsyncConnection) -> None:
    tenant = TenantContext(job.workspace_id)
    settings = job_settings()
    if not await RuleRepository(conn, tenant).has_enabled(m.SCHEDULED_TRIGGERS):
        return  # nothing to watch; the next scheduled rule written starts the timer again
    # The successor first, so a slow check cannot stretch the interval; `sweep` itself never
    # raises on a failing check, which would roll this back and end the chain.
    await OutboxRepository(conn, tenant).enqueue(
        job_type=ALERT_SWEEP,
        dedupe_key=str(tenant.workspace_id),
        payload={},
        delay=timedelta(seconds=settings.alerts_sweep_interval_seconds),
    )
    await sweep(conn, tenant, now=system_clock(), skipped_cap=settings.alerts_skipped_per_window)
