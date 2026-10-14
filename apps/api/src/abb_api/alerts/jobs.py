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

`deliver_alerts` sends the workspace's due `pending` notifications (`delivery.py`). Evaluation asks
for it (coalesced per workspace) whenever it writes a pending row; a tick that leaves rows waiting
for a retry schedules a retry job at the earliest due time under a second key, so a retry delay
never holds back a fresh alert (the two keys coalesce separately, and the earlier time wins).

`alert_prune` deletes finished notifications older than the retention window, a bounded batch at a
time. It is armed when a notification row is written and re-arms itself while rows remain.
"""

import logging
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.alerts import model as m
from abb_api.alerts.delivery import deliver_due, registry_for
from abb_api.alerts.evaluate import evaluate_event, sweep
from abb_api.alerts.repository import NotificationRepository, RuleRepository
from abb_api.alerts.schedule import ensure_prune, request_delivery, schedule_retry
from abb_api.clock import system_clock
from abb_api.jobs.context import job_settings
from abb_api.jobs.outbox import ALERT_PRUNE, ALERT_SWEEP, EVALUATE_ALERTS, Job, OutboxRepository
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


# ---------------------------------------------------------------- delivery and pruning


async def deliver_alerts(job: Job, conn: AsyncConnection) -> None:
    settings = job_settings()
    tenant = TenantContext(job.workspace_id)
    if not settings.alerts_delivery_enabled:
        return  # an operator hold: rows stay pending until it is lifted and a tick runs
    now = system_clock()
    result = await deliver_due(
        conn, tenant, now=now, settings=settings, registry=registry_for(settings)
    )
    if result.claimed == 0:
        return
    outbox = OutboxRepository(conn, tenant)
    next_at = await NotificationRepository(conn, tenant).next_pending_at()
    if next_at is None:
        return
    if next_at <= now:
        await request_delivery(outbox, tenant)  # the batch was full: carry on in the next tick
    else:
        await schedule_retry(conn, tenant, next_at + timedelta(seconds=1))


async def alert_prune(job: Job, conn: AsyncConnection) -> None:
    settings = job_settings()
    tenant = TenantContext(job.workspace_id)
    notifications = NotificationRepository(conn, tenant)
    cutoff = system_clock() - timedelta(days=settings.alerts_retention_days)
    deleted = await notifications.prune(cutoff, settings.alerts_prune_batch)
    if deleted:
        logger.info("alert history pruned", extra={"deleted": deleted})
    outbox = OutboxRepository(conn, tenant)
    if deleted >= settings.alerts_prune_batch:
        await outbox.enqueue(
            job_type=ALERT_PRUNE,
            dedupe_key=str(tenant.workspace_id),
            payload={},
            delay=timedelta(seconds=5),
        )
    elif await notifications.any_left():
        await ensure_prune(
            outbox, tenant, timedelta(seconds=settings.alerts_prune_interval_seconds)
        )
