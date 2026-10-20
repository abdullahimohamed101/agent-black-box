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
from abb_api.core.config import Settings
from abb_api.jobs.context import job_metrics, job_settings
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
    tenant = TenantContext(job.workspace_id)
    await evaluate_event(
        conn,
        tenant,
        str(trigger),
        subject_id,
        now=system_clock(),
        skipped_cap=settings.alerts_skipped_per_window,
        max_age=timedelta(days=settings.alerts_retention_days),
    )
    await rearm_sweep(conn, tenant)


async def alert_sweep(job: Job, conn: AsyncConnection) -> None:
    tenant = TenantContext(job.workspace_id)
    settings = job_settings()
    if not await RuleRepository(conn, tenant).has_enabled(m.SCHEDULED_TRIGGERS):
        return  # nothing to watch; the next scheduled rule written starts the timer again
    # `sweep` itself never raises on a failing check, which would roll this back and end the chain.
    await sweep(
        conn,
        tenant,
        now=system_clock(),
        skipped_cap=settings.alerts_skipped_per_window,
        grace=timedelta(seconds=2 * settings.alerts_sweep_interval_seconds),
    )
    # The successor goes LAST (review P2-4): every writer takes its rule locks before it inserts
    # outbox rows, and this job must too. Inserting the successor first and locking rules in the
    # checks after it could deadlock with an API rule edit, which holds the rule's row and then
    # inserts the same sweep key (a unique-index conflict waits for our commit while we wait for
    # its row lock). The interval therefore starts after the checks, which are bounded.
    await OutboxRepository(conn, tenant).enqueue(
        job_type=ALERT_SWEEP,
        dedupe_key=str(tenant.workspace_id),
        payload={},
        delay=timedelta(seconds=settings.alerts_sweep_interval_seconds),
    )


async def rearm_sweep(conn: AsyncConnection, tenant: TenantContext) -> None:
    """Restart the workspace's scheduled-check timer if it has scheduled rules and no pending job.
    Called last by the other alert jobs (and so after any rule lock they took), it makes a chain
    that was dead-lettered or lost to a long outage heal itself while the workspace has alert
    traffic; coalescing makes it a no-op when the timer is already pending."""
    if await RuleRepository(conn, tenant).has_enabled(m.SCHEDULED_TRIGGERS):
        await ensure_sweep(OutboxRepository(conn, tenant), tenant)


# ---------------------------------------------------------------- delivery and pruning


async def deliver_alerts(job: Job, conn: AsyncConnection) -> None:
    settings = job_settings()
    tenant = TenantContext(job.workspace_id)
    if not settings.alerts_delivery_enabled:
        return  # an operator hold: rows stay pending until it is lifted and a tick runs
    await _deliver_tick(conn, tenant, settings)
    await rearm_sweep(conn, tenant)  # last: after the destination locks the tick took


async def _deliver_tick(conn: AsyncConnection, tenant: TenantContext, settings: Settings) -> None:
    now = system_clock()
    result = await deliver_due(
        conn,
        tenant,
        now=now,
        settings=settings,
        registry=registry_for(settings),
        metrics=job_metrics(),
    )
    outbox = OutboxRepository(conn, tenant)
    next_at = await NotificationRepository(conn, tenant).next_pending_at()
    if next_at is None:
        return
    if next_at <= now and result.claimed >= settings.alerts_deliver_batch:
        await request_delivery(outbox, tenant)  # the batch was full: carry on in the next tick
        return
    # Otherwise wake up when the earliest row is due: it is waiting for a retry, or we were woken
    # early by a clock difference, or another tick holds the rows we could not take (look again
    # in a few seconds, not in a loop).
    quick = next_at > now or result.claimed > 0
    await schedule_retry(conn, tenant, max(next_at, now) + timedelta(seconds=1 if quick else 5))


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
