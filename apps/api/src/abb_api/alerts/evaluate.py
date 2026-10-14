"""Rule evaluation (ADR-150): match one fact against the workspace's enabled rules and write
notification rows. No delivery happens here (step 2 sends the `pending` rows).

Entry points, all idempotent and workspace-scoped (INV-3):

* `evaluate_event`: the `evaluate_alerts` job body for a run that failed, a security finding that
  appeared or got worse, an approval that was requested. The producers only enqueue a small job
  keyed by (trigger, subject id); the matching runs here, off the hot path.
* `sweep`: the scheduled checks, an approval close to expiring and a daily spend over a threshold.

Dedupe. A notification's identity is `(workspace, rule, destination, dedupe_key)`, enforced by a
unique index: a redelivered job, a rebuild or a second summary of the same run inserts nothing.
Keys: run `run_id:STATUS`; finding `finding_id:detector_version:severity`; approval
`approval_id:requested` / `approval_id:expiring`; spend `rule_id:YYYY-MM-DD` (UTC day).

Cooldown. Per rule and destination: while a real (not skipped) notification created within the
last `cooldown_seconds` exists, a new event for that pair is recorded as `skipped` with reason
`cooldown`, so the history is honest about what was suppressed, and counted against a per-window
cap (`alerts_skipped_per_window`) beyond which only a log line remains. A skipped event does not
fire later: its dedupe key is spent. A paused destination likewise records `destination_paused`.
Each rule is locked (`FOR UPDATE`) while it is evaluated, so two events racing through one rule
cannot both pass the cooldown check.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from abb_event_schema.ids import IdKind
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.alerts import model as m
from abb_api.alerts.payload import build_payload
from abb_api.alerts.repository import NotificationRepository, RuleRepository
from abb_api.alerts.schedule import ensure_prune, request_delivery
from abb_api.alerts.spend import SpendReader
from abb_api.alerts.subjects import SubjectLoader
from abb_api.ids import new_uuid
from abb_api.jobs.outbox import OutboxRepository
from abb_api.tenancy import TenantContext

logger = logging.getLogger(__name__)

EXPIRING_BATCH = 500


@dataclass
class Outcome:
    created: int = 0
    skipped: int = 0
    dropped: int = 0  # suppressed past the per-window cap: logged, not stored


def rule_matches(rule: m.RuleRow, subject: m.Subject) -> bool:
    """Whether a rule wants this fact. Pure: filters are compared with the subject's stored
    metadata and nothing else."""
    if rule.trigger != subject.trigger or not rule.enabled:
        return False
    if rule.project_id is not None and rule.project_id != subject.project_id:
        return False
    agent = rule.filters.get("agent")
    if agent is not None and agent != subject.agent_slug:
        return False
    if subject.trigger == m.SECURITY_FINDING:
        minimum = m.SEVERITY_RANK.get(str(rule.filters.get("min_severity")), 0)
        return m.SEVERITY_RANK.get(subject.finding_severity or "", -1) >= minimum
    if subject.trigger == m.RUN_FAILED:
        wanted = rule.filters.get("run_statuses") or list(m.RUN_FAIL_STATUSES)
        return subject.run_status in wanted
    if subject.trigger == m.APPROVAL_EXPIRING:
        lead = rule.filters.get("expiring_lead_seconds")
        return (
            isinstance(lead, int)
            and subject.seconds_to_expiry is not None
            and subject.seconds_to_expiry <= lead
        )
    return True


async def record(
    conn: AsyncConnection,
    tenant: TenantContext,
    subject: m.Subject,
    targets: list[m.RuleTarget],
    *,
    now: datetime,
    skipped_cap: int,
) -> Outcome:
    """Write the notification rows of `subject` for the given (already matched) rules."""
    rules = RuleRepository(conn, tenant)
    notifications = NotificationRepository(conn, tenant)
    outcome = Outcome()
    for target in sorted(targets, key=lambda x: x.rule.id.bytes):
        rule = target.rule
        if not await rules.lock(rule.id):
            continue  # switched off or deleted since it was loaded
        fired = False
        for destination_id, state in target.destinations:
            if await notifications.exists(rule.id, destination_id, subject.dedupe_key):
                continue
            reason: str | None = None
            if state == m.PAUSED:
                reason = "destination_paused"
            elif rule.cooldown_seconds > 0 and await notifications.made_since(
                rule.id, destination_id, now - timedelta(seconds=rule.cooldown_seconds)
            ):
                reason = "cooldown"
            if reason is not None:
                window = timedelta(seconds=max(rule.cooldown_seconds, 3600))
                if (
                    await notifications.skipped_since(rule.id, destination_id, now - window)
                    >= skipped_cap
                ):
                    outcome.dropped += 1
                    logger.info(
                        "alert suppressed beyond the history cap",
                        extra={"reason": reason, "trigger": subject.trigger},
                    )
                    continue
            alert_id = new_uuid(IdKind.ALERT)
            created = await notifications.insert(
                id=alert_id,
                rule_id=rule.id,
                destination_id=destination_id,
                trigger=subject.trigger,
                dedupe_key=subject.dedupe_key,
                project_id=subject.project_id,
                run_id=subject.run_id,
                finding_id=subject.finding_id,
                approval_id=subject.approval_id,
                agent_slug=subject.agent_slug,
                severity=subject.severity,
                payload_json=build_payload(subject, alert_id=alert_id, rule=rule, created_at=now),
                status=m.SKIPPED if reason else m.PENDING,
                skip_reason=reason,
                next_attempt_at=now,
                created_at=now,
            )
            if not created:
                continue
            if reason:
                outcome.skipped += 1
            else:
                outcome.created += 1
                fired = True
        if fired:
            await rules.mark_fired(rule.id, now)
    if outcome.created or outcome.skipped:
        outbox = OutboxRepository(conn, tenant)
        if outcome.created:
            await request_delivery(outbox, tenant)
        await ensure_prune(outbox, tenant)
    return outcome


async def evaluate_subject(
    conn: AsyncConnection,
    tenant: TenantContext,
    subject: m.Subject,
    *,
    now: datetime,
    skipped_cap: int,
) -> Outcome:
    targets = [
        x
        for x in await RuleRepository(conn, tenant).enabled_targets(subject.trigger)
        if rule_matches(x.rule, subject)
    ]
    if not targets:
        return Outcome()
    return await record(conn, tenant, subject, targets, now=now, skipped_cap=skipped_cap)


async def evaluate_event(
    conn: AsyncConnection,
    tenant: TenantContext,
    trigger: str,
    subject_id: uuid.UUID,
    *,
    now: datetime,
    skipped_cap: int,
    max_age: timedelta | None = None,
) -> Outcome:
    """The `evaluate_alerts` job: load the fact as it is NOW (a run that recovered, a finding that
    was deleted or an approval that was decided in the meantime is simply not alerted) and match.
    A failed run that ended more than `max_age` ago (the history retention) is not announced."""
    loader = SubjectLoader(conn, tenant)
    slug = await loader.workspace_slug()
    subject: m.Subject | None
    if trigger == m.RUN_FAILED:
        subject = await loader.run_failed(
            subject_id, slug, not_before=now - max_age if max_age is not None else None
        )
    elif trigger == m.SECURITY_FINDING:
        subject = await loader.security_finding(subject_id, slug)
    elif trigger == m.APPROVAL_REQUESTED:
        subject = await loader.approval(subject_id, slug)
    else:
        return Outcome()  # scheduled triggers are never event-driven
    if subject is None:
        return Outcome()
    return await evaluate_subject(conn, tenant, subject, now=now, skipped_cap=skipped_cap)


async def sweep(
    conn: AsyncConnection, tenant: TenantContext, *, now: datetime, skipped_cap: int
) -> Outcome:
    """The scheduled checks of one workspace. Each check runs in a savepoint: one failing never
    stops the other, and the caller (the sweep job) must not fail on it, because a failed job
    would end the chain of timers."""
    total = Outcome()
    for name, check in (("expiring", expiring_check), ("spend", spend_check)):
        try:
            async with conn.begin_nested():
                part = await check(conn, tenant, now=now, skipped_cap=skipped_cap)
        except Exception as exc:
            # Only the class: the message may quote a driver parameter.
            logger.warning(
                "alert check failed", extra={"check": name, "error_type": type(exc).__name__}
            )
            continue
        total.created += part.created
        total.skipped += part.skipped
        total.dropped += part.dropped
    return total


async def expiring_check(
    conn: AsyncConnection, tenant: TenantContext, *, now: datetime, skipped_cap: int
) -> Outcome:
    rules = RuleRepository(conn, tenant)
    targets = await rules.enabled_targets(m.APPROVAL_EXPIRING)
    if not targets:
        return Outcome()
    longest = max(int(x.rule.filters.get("expiring_lead_seconds") or 0) for x in targets)
    loader = SubjectLoader(conn, tenant)
    subjects = await loader.expiring_approvals(
        await loader.workspace_slug(),
        now=now,
        until=now + timedelta(seconds=longest),
        limit=EXPIRING_BATCH,
    )
    total = Outcome()
    for subject in subjects:
        matched = [x for x in targets if rule_matches(x.rule, subject)]
        if not matched:
            continue
        part = await record(conn, tenant, subject, matched, now=now, skipped_cap=skipped_cap)
        total.created += part.created
        total.skipped += part.skipped
        total.dropped += part.dropped
    return total


async def spend_check(
    conn: AsyncConnection, tenant: TenantContext, *, now: datetime, skipped_cap: int
) -> Outcome:
    targets = await RuleRepository(conn, tenant).enabled_targets(m.SPEND_OVER)
    if not targets:
        return Outcome()
    day = now.astimezone(UTC).date()
    loader = SubjectLoader(conn, tenant)
    slug = await loader.workspace_slug()
    reader = SpendReader(conn, tenant)
    total = Outcome()
    for target in targets:
        rule = target.rule
        threshold = Decimal(str(rule.filters.get("threshold_usd", 0)))
        agent = rule.filters.get("agent")
        spent = await reader.day_total(day, project_id=rule.project_id, agent_slug=agent)
        if threshold <= 0 or spent <= threshold:
            continue  # "over": strictly more than the threshold
        project = await loader.project(rule.project_id) if rule.project_id else None
        subject = m.Subject(
            trigger=m.SPEND_OVER,
            dedupe_key=f"{rule.id}:{day.isoformat()}",
            severity="warning",
            workspace_id=tenant.workspace_id,
            workspace_slug=slug,
            project_id=rule.project_id,
            project_slug=project[0] if project else None,
            project_name=project[1] if project else None,
            agent_slug=agent,
            agent_name=await loader.agent_name(rule.project_id, agent) if agent else None,
            details={
                "day": day.isoformat(),
                "spend_usd": float(round(spent, 6)),
                "threshold_usd": float(threshold),
                "period": "day",
            },
        )
        part = await record(conn, tenant, subject, [target], now=now, skipped_cap=skipped_cap)
        total.created += part.created
        total.skipped += part.skipped
        total.dropped += part.dropped
    return total
