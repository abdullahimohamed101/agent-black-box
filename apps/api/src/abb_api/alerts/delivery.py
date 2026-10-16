"""Delivery of `pending` notifications (ADR-151).

One tick (`deliver_due`) runs inside an `deliver_alerts` outbox job for one workspace:

* **Claim.** At most `alerts_deliver_batch` due rows are locked with `FOR UPDATE SKIP LOCKED`
  (the global per-tick bound; a tick that fills the batch asks for another). A worker that dies
  mid-tick rolls the transaction back, the rows are pending again and are sent again: delivery is
  **at least once**, and a receiver dedupes on `alert_id` (the notification id, also sent as the
  `X-ABB-Alert-Id` header and in the body).
* **Re-check.** The destination must still exist and not be paused, and the rule must still
  exist; otherwise the row is closed as `skipped` (`destination_deleted`, `destination_paused`,
  `rule_deleted`). A destination at its per-minute rate limit or its daily cap closes the row as
  `skipped` (`rate_limited`, `daily_cap`): excess is dropped, never queued, so a flood cannot grow
  a backlog (the history shows exactly what was not sent and why).
* **Send.** The destination's sealed columns are opened here and nowhere else, handed to the
  `Notifier` of its type, and dropped. One failure on a destination stops further attempts to it
  in the same tick (the other due rows are pushed out without spending an attempt), so a down
  receiver gets one request per tick, not a burst.
* **Outcome.** `delivered`; or a retryable failure with exponential backoff and jitter, up to
  `alerts_max_attempts`; or `dead` with a fixed error code (also at once for a failure no retry
  will fix: a refused address, a TLS or 4xx answer, an unreadable secret).
* **Health.** Failed attempts add to the destination's `consecutive_failures`, a success resets it.
  `failing` from `alerts_failing_after`, auto-paused (`paused_reason = failures`, audited by the
  system actor) from `alerts_pause_after_failures`. A person resumes it.

The network wait happens while the job's transaction is open (rows stay locked, so no other tick
can take them); the worker's lease heartbeat covers the longest tick (batch x request timeout).
"""

import logging
import random
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from abb_event_schema.ids import IdKind, from_uuid
from sqlalchemy.ext.asyncio import AsyncConnection

from abb_api.alerts import model as m
from abb_api.alerts import outbound as o
from abb_api.alerts import sealed
from abb_api.alerts.notifiers import Alert, OpenDestination, Registry, SendResult, build_registry
from abb_api.alerts.repository import (
    DestinationRepository,
    NotificationRepository,
    RuleRepository,
)
from abb_api.audit.repository import AuditEntry, AuditRepository
from abb_api.core.config import Settings
from abb_api.obs.catalogue import METRICS, Catalogue
from abb_api.tenancy import TenantContext

logger = logging.getLogger(__name__)

SYSTEM_ACTOR = "alert-delivery"
AUTO_PAUSE_ACTION = "alert.destination.auto_pause"
_RATE_WINDOW = timedelta(minutes=1)
_DAY = timedelta(days=1)
_JITTER = 0.25


@dataclass
class TickResult:
    claimed: int = 0
    delivered: int = 0
    retried: int = 0
    dead: int = 0
    skipped: int = 0
    deferred: int = 0
    paused: list[uuid.UUID] = field(default_factory=list)


@dataclass
class _Health:
    reset: bool = False
    failures: int = 0


def backoff_for(attempt: int, settings: Settings, rng: Callable[[], float]) -> timedelta:
    """Delay before attempt `attempt + 1`: base * 2^(attempt-1), capped, +-25% jitter."""
    raw = min(
        settings.alerts_backoff_max_seconds,
        settings.alerts_backoff_base_seconds * (2 ** (attempt - 1)),
    )
    return timedelta(seconds=raw * (1 + _JITTER * (2 * rng() - 1)))


async def open_destination(
    conn: AsyncConnection, workspace_id: uuid.UUID, row: m.DestinationRow
) -> OpenDestination:
    """Open a destination's sealed columns. Raises `SealError` or `ValueError` when unreadable."""
    engine = conn.engine
    config = sealed.decode_config(
        await sealed.open_sealed(
            engine,
            row.config_encrypted,
            purpose="config",
            workspace_id=workspace_id,
            destination_id=row.id,
        )
    )
    secret = None
    if row.signing_secret_encrypted is not None:
        secret = (
            await sealed.open_sealed(
                engine,
                row.signing_secret_encrypted,
                purpose="signing",
                workspace_id=workspace_id,
                destination_id=row.id,
            )
        ).decode()
    return OpenDestination(id=row.id, type=row.type, config=config, signing_secret=secret)


async def send_opened(registry: Registry, destination: OpenDestination, alert: Alert) -> SendResult:
    """Hand an alert to the notifier of the destination's type; never raises."""
    notifier = registry.get(destination.type)
    if notifier is None:
        return SendResult(False, o.INTERNAL_ERROR)
    try:
        return await notifier.send(destination, alert)
    except Exception as exc:  # a notifier bug must not break the tick; class name only
        logger.warning("notifier raised", extra={"error_type": type(exc).__name__})
        return SendResult(False, o.INTERNAL_ERROR, retryable=True)


async def deliver_due(
    conn: AsyncConnection,
    tenant: TenantContext,
    *,
    now: datetime,
    settings: Settings,
    registry: Registry,
    rng: Callable[[], float] = random.random,
    metrics: Catalogue = METRICS,
) -> TickResult:
    """One delivery tick. Every notification that reaches a final status or a failed attempt is
    counted once in ``alert_deliveries_total{status,reason}`` (reason: the fixed error code or skip
    reason, "none" for a delivery); a deferral spends no attempt and is not counted."""
    deliveries = metrics.alert_deliveries_total
    notifications = NotificationRepository(conn, tenant)
    destinations = DestinationRepository(conn, tenant)
    result = TickResult()
    due = await notifications.claim_due(now, settings.alerts_deliver_batch)
    result.claimed = len(due)
    if not due:
        return result
    known = await destinations.get_many([d.destination_id for d in due if d.destination_id])
    live_rules = await RuleRepository(conn, tenant).existing_ids(
        [d.rule_id for d in due if d.rule_id]
    )
    health: dict[uuid.UUID, _Health] = {}
    failed_now: set[uuid.UUID] = set()
    sent_minute: dict[uuid.UUID, int] = {}
    sent_day: dict[uuid.UUID, int] = {}

    for item in due:
        destination = known.get(item.destination_id) if item.destination_id else None
        if destination is None:
            await notifications.mark_skipped(item.id, "destination_deleted")
            deliveries.inc("skipped", "destination_deleted")
            result.skipped += 1
            continue
        if item.rule_id is None or item.rule_id not in live_rules:
            await notifications.mark_skipped(item.id, "rule_deleted")
            deliveries.inc("skipped", "rule_deleted")
            result.skipped += 1
            continue
        if destination.state == m.PAUSED:
            await notifications.mark_skipped(item.id, "destination_paused")
            deliveries.inc("skipped", "destination_paused")
            result.skipped += 1
            continue
        if destination.id not in sent_day:
            sent_minute[destination.id] = await notifications.delivered_since(
                destination.id, now - _RATE_WINDOW
            )
            sent_day[destination.id] = await notifications.delivered_since(
                destination.id, now - _DAY
            )
        if sent_day[destination.id] >= settings.alerts_daily_cap:
            await notifications.mark_skipped(item.id, "daily_cap")
            deliveries.inc("skipped", "daily_cap")
            result.skipped += 1
            continue
        if sent_minute[destination.id] >= settings.alerts_rate_per_minute:
            await notifications.mark_skipped(item.id, "rate_limited")
            deliveries.inc("skipped", "rate_limited")
            result.skipped += 1
            continue
        if destination.id in failed_now:
            await notifications.defer(
                item.id, now + backoff_for(max(item.attempts, 1), settings, rng)
            )
            result.deferred += 1
            continue

        attempt = item.attempts + 1
        outcome = await _attempt(conn, tenant, registry, settings, destination, item, attempt)
        stats = health.setdefault(destination.id, _Health())
        if outcome.ok:
            await notifications.mark_delivered(item.id, attempt, now)
            deliveries.inc("delivered", "none")
            stats.reset, stats.failures = True, 0
            sent_minute[destination.id] += 1
            sent_day[destination.id] += 1
            result.delivered += 1
            continue
        stats.failures += 1
        failed_now.add(destination.id)
        code = outcome.error_code or o.INTERNAL_ERROR
        if outcome.retryable and attempt < settings.alerts_max_attempts:
            await notifications.mark_retry(
                item.id, attempt, code, now + backoff_for(attempt, settings, rng)
            )
            deliveries.inc("failed", code)
            result.retried += 1
        else:
            await notifications.mark_dead(item.id, attempt, code)
            deliveries.inc("dead", code)
            result.dead += 1
            logger.warning(
                "alert dead-lettered",
                extra={"alert_id": str(item.id), "error_code": code, "attempts": attempt},
            )

    audit = AuditRepository(conn, tenant)
    # In one global order (by id): the tick of `<ws>` and the tick of `<ws>:retry` can overlap, and
    # each takes a row lock per destination here; first-seen order would let two ticks deadlock.
    for destination_id, stats in sorted(health.items(), key=lambda item: item[0].bytes):
        applied = await destinations.apply_health(
            destination_id,
            reset=stats.reset,
            failures=stats.failures,
            failing_after=settings.alerts_failing_after,
            pause_after=settings.alerts_pause_after_failures,
        )
        if applied is None:
            continue
        previous, state, count = applied
        if state == m.PAUSED and previous != m.PAUSED:
            result.paused.append(destination_id)
            await audit.append(
                AuditEntry(
                    actor_kind="system",
                    actor_id=SYSTEM_ACTOR,
                    action=AUTO_PAUSE_ACTION,
                    resource_kind="alert_destination",
                    resource_id=from_uuid(IdKind.DESTINATION, destination_id),
                    details={"reason": "failures", "consecutive_failures": count},
                )
            )
            logger.warning("alert destination paused after repeated failures")
    return result


async def _attempt(
    conn: AsyncConnection,
    tenant: TenantContext,
    registry: Registry,
    settings: Settings,
    destination: m.DestinationRow,
    item: m.DueNotification,
    attempt: int,
) -> SendResult:
    try:
        opened = await open_destination(conn, tenant.workspace_id, destination)
    except (sealed.SealError, ValueError):
        return SendResult(False, o.CONFIG_UNREADABLE)
    alert = Alert(
        alert_id=from_uuid(IdKind.ALERT, item.id),
        payload=item.payload,
        attempt=attempt,
        web_origin=settings.web_origin_normalised,
    )
    return await send_opened(registry, opened, alert)


# ---------------------------------------------------------------- the registry a worker uses

_override: Registry | None = None
_default: tuple[Settings, Registry] | None = None


def registry_for(settings: Settings) -> Registry:
    global _default
    if _override is not None:
        return _override
    if _default is None or _default[0] is not settings:
        _default = (settings, build_registry(settings))
    return _default[1]


@contextmanager
def use_registry(registry: Registry) -> Iterator[None]:
    """Tests: deliver through these notifiers (local receivers, stub resolver)."""
    global _override
    previous, _override = _override, registry
    try:
        yield
    finally:
        _override = previous
