"""Delivery of alert notifications (ADR-151) against local receivers: signed webhook, retries with
backoff and dedupe on `alert_id`, dead letters with fixed codes, auto-pause and recovery, the retry
storm bound, rate limit and daily cap, the re-check at send time, tenancy, concurrency, pruning."""

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from abb_event_schema.ids import IdKind, from_uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from abb_api.alerts import model as m
from abb_api.alerts.delivery import backoff_for, deliver_due
from abb_api.alerts.jobs import alert_prune, deliver_alerts
from abb_api.alerts.notifiers import Registry, build_registry
from abb_api.alerts.repository import DestinationRepository, NotificationRepository, RuleRepository
from abb_api.alerts.schedule import request_delivery
from abb_api.core.config import Settings
from abb_api.ids import new_uuid
from abb_api.jobs.context import reset_job_settings, set_job_settings
from abb_api.jobs.handlers import HANDLERS
from abb_api.jobs.outbox import ALERT_PRUNE, DELIVER_ALERTS, Job, OutboxRepository
from abb_api.jobs.worker import Worker
from abb_api.tenancy import TenantContext
from tests.alerts_helpers import SENTINEL_SIGNING, rows, seed_destination, seed_rule
from tests.alerts_receivers import (
    HOST,
    PUBLIC_IP,
    RESPONSE_SENTINEL,
    Request,
    SmtpSink,
    TlsReceiver,
)
from tests.api_fixtures import Api, person
from tests.conftest import make_settings, runtime_url

T0 = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
ORIGIN = "https://abb.example.com"
HALF = lambda: 0.5  # noqa: E731 - the jitter factor is exactly 1.0


def dsettings(api: Api | None = None, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "alerts_delivery_enabled": True,
        "web_origin": ORIGIN,
        "alerts_backoff_base_seconds": 30.0,
        "alerts_backoff_max_seconds": 1800.0,
        "alerts_max_attempts": 5,
        "alerts_rate_per_minute": 1000,
        "alerts_daily_cap": 100000,
    }
    return make_settings("postgresql+asyncpg://x:y@localhost/z", **{**base, **overrides})


class Rig:
    """One workspace with a destination pointing at a local receiver and one rule."""

    def __init__(self, api: Api, receiver: TlsReceiver, settings: Settings, registry: Registry):
        self.api, self.receiver, self.settings, self.registry = api, receiver, settings, registry
        self.tenant = api.tenant.context
        self.destination_id: uuid.UUID
        self.rule_id: uuid.UUID

    async def setup(self, **destination: Any) -> "Rig":
        self.destination_id = await seed_destination(
            self.api.engine, self.tenant, url=self.receiver.url(), **destination
        )
        self.rule_id = await seed_rule(self.api.engine, self.tenant, [self.destination_id])
        return self

    async def add(
        self, n: int = 1, *, at: datetime = T0, destination: uuid.UUID | None = None, **kw: Any
    ) -> list[uuid.UUID]:
        return await pending(
            self.api, self.rule_id, destination or self.destination_id, n=n, at=at, **kw
        )

    async def tick(self, at: datetime = T0, **settings: Any) -> Any:
        used = self.settings.model_copy(update=settings) if settings else self.settings
        async with self.api.engine.begin() as conn:
            return await deliver_due(
                conn, self.tenant, now=at, settings=used, registry=self.registry, rng=HALF
            )

    async def row(self, notification_id: uuid.UUID) -> Any:
        found = await rows(self.api, f"id = '{notification_id}'")
        return found[0]

    async def destination(self) -> Any:
        async with self.api.engine.connect() as conn:
            return (
                await conn.execute(
                    text("SELECT * FROM alert_destinations WHERE id = :i"),
                    {"i": self.destination_id},
                )
            ).one()


async def pending(
    api: Api,
    rule_id: uuid.UUID | None,
    destination_id: uuid.UUID | None,
    *,
    n: int = 1,
    at: datetime = T0,
    status: str = "pending",
    skip_reason: str | None = None,
    tenant: TenantContext | None = None,
    attempts: int = 0,
    delivered_at: datetime | None = None,
    created_at: datetime | None = None,
) -> list[uuid.UUID]:
    made = []
    async with api.engine.begin() as conn:
        repository = NotificationRepository(conn, tenant or api.tenant.context)
        for i in range(n):
            nid = new_uuid(IdKind.ALERT)
            payload = {
                "alert_id": from_uuid(IdKind.ALERT, nid),
                "kind": "run_failed",
                "severity": "high",
                "title": "Run failed",
                "workspace_id": "ws_x",
                "project": {"id": "prj_x", "slug": "alpha", "name": "Alpha"},
                "agent": None,
                "rule": {"id": "rul_x", "name": "Failures"},
                "subject": {"run_id": "run_x"},
                "details": {"run_status": "FAILED"},
                "link_path": "/w/acme/projects/alpha/runs/run_x",
                "created_at": at.isoformat(),
            }
            await repository.insert(
                id=nid,
                rule_id=rule_id,
                destination_id=destination_id,
                trigger="run_failed",
                dedupe_key=f"k{nid}-{i}",
                severity="high",
                payload_json=payload,
                status=status,
                skip_reason=skip_reason,
                attempts=attempts,
                next_attempt_at=at,
                created_at=created_at or at,
                delivered_at=delivered_at,
            )
            made.append(nid)
    return made


@pytest.fixture
async def rig_factory(web: Api) -> AsyncIterator[Any]:
    api = web  # the API with cookie sessions, so a test can read the history as a person
    receivers: list[TlsReceiver] = []

    async def make(
        responder: Any = None, *, receiver: TlsReceiver | None = None, **settings: Any
    ) -> Rig:
        receiver = receiver or TlsReceiver(**({"responder": responder} if responder else {}))
        if receiver.port == 0:
            await receiver.__aenter__()
        receivers.append(receiver)
        config = dsettings(api, **settings)
        registry = build_registry(config, receiver.sender())
        return await Rig(api, receiver, config, registry).setup()

    yield make
    for receiver in receivers:
        await receiver.__aexit__()


def ids_seen(receiver: TlsReceiver) -> list[str]:
    return [r.headers["x-abb-alert-id"] for r in receiver.requests]


# ---------------------------------------------------------------- the happy path


async def test_a_due_notification_is_delivered_once_with_a_signature_the_receiver_verifies(
    rig_factory: Any,
) -> None:
    rig: Rig = await rig_factory()
    (nid,) = await rig.add()
    result = await rig.tick()
    assert (result.claimed, result.delivered) == (1, 1)
    (request,) = rig.receiver.requests
    expected = hmac.new(
        SENTINEL_SIGNING.encode(),
        request.headers["x-abb-timestamp"].encode() + b"." + request.body,
        hashlib.sha256,
    ).hexdigest()
    assert request.headers["x-abb-signature"] == f"v1={expected}"
    body = json.loads(request.body)
    assert body["alert_id"] == from_uuid(IdKind.ALERT, nid) == request.headers["x-abb-alert-id"]
    assert body["link"] == f"{ORIGIN}/w/acme/projects/alpha/runs/run_x"
    row = await rig.row(nid)
    assert (row.status, row.attempts, row.delivered_at, row.last_error_code) == (
        "delivered", 1, T0, None,
    )  # fmt: skip
    assert (await rig.tick(T0 + timedelta(hours=1))).claimed == 0
    assert len(rig.receiver.requests) == 1  # nothing is sent twice


async def test_skipped_and_future_rows_are_never_sent(rig_factory: Any) -> None:
    rig: Rig = await rig_factory()
    await rig.add(status="skipped", skip_reason="cooldown")
    await rig.add(at=T0 + timedelta(minutes=5))
    result = await rig.tick()
    assert result.claimed == 0 and rig.receiver.requests == []


# ---------------------------------------------------------------- reliability


async def test_a_receiver_that_is_down_gets_bounded_retries_then_the_alert_once_back(
    rig_factory: Any,
) -> None:
    calls = {"n": 0}

    def flaky(_: Request) -> tuple[int, dict[str, str]]:
        calls["n"] += 1
        return (503, {}) if calls["n"] <= 2 else (200, {})

    rig: Rig = await rig_factory(flaky)
    (nid,) = await rig.add()
    first = await rig.tick(T0)
    assert (first.retried, first.delivered) == (1, 0)
    row = await rig.row(nid)
    assert (row.status, row.attempts, row.last_error_code) == ("pending", 1, "http_5xx")
    assert row.next_attempt_at == T0 + timedelta(seconds=30)  # base, jitter factor 1.0
    assert (await rig.tick(T0 + timedelta(seconds=10))).claimed == 0  # not due yet
    second = await rig.tick(T0 + timedelta(seconds=31))
    assert second.retried == 1
    assert (await rig.row(nid)).next_attempt_at == T0 + timedelta(seconds=31 + 60)  # doubled
    third = await rig.tick(T0 + timedelta(seconds=100))
    assert third.delivered == 1
    row = await rig.row(nid)
    assert (row.status, row.attempts, row.last_error_code) == ("delivered", 3, None)
    # Three requests, one alert: the receiver dedupes on the same id every time.
    assert Counter(ids_seen(rig.receiver)) == {from_uuid(IdKind.ALERT, nid): 3}
    assert [r.headers["x-abb-delivery-attempt"] for r in rig.receiver.requests] == ["1", "2", "3"]
    assert (await rig.tick(T0 + timedelta(hours=1))).claimed == 0


async def test_backoff_doubles_is_capped_and_jittered_within_bounds() -> None:
    settings = dsettings(alerts_backoff_base_seconds=10.0, alerts_backoff_max_seconds=100.0)
    middle = [backoff_for(a, settings, HALF).total_seconds() for a in range(1, 8)]
    assert middle == [10, 20, 40, 80, 100, 100, 100]
    low = backoff_for(1, settings, lambda: 0.0).total_seconds()
    high = backoff_for(1, settings, lambda: 1.0).total_seconds()
    assert (low, high) == (7.5, 12.5)


async def test_an_alert_that_keeps_failing_is_dead_lettered_at_the_cap_with_a_fixed_code(
    rig_factory: Any,
) -> None:
    rig: Rig = await rig_factory(lambda _: (500, {}), alerts_max_attempts=3)
    (nid,) = await rig.add()
    at = T0
    for _ in range(6):
        await rig.tick(at)
        at += timedelta(hours=1)
    row = await rig.row(nid)
    assert (row.status, row.attempts, row.last_error_code) == ("dead", 3, "http_5xx")
    assert len(rig.receiver.requests) == 3  # the cap, not one more
    assert RESPONSE_SENTINEL not in json.dumps(
        {k: str(v) for k, v in row._mapping.items()}
    )  # a response body is never stored


@pytest.mark.parametrize(
    ("status", "code"), [(400, "http_4xx"), (401, "http_4xx"), (404, "http_4xx"), (302, "http_3xx")]
)
async def test_a_failure_no_retry_can_fix_is_dead_at_once(
    rig_factory: Any, status: int, code: str
) -> None:
    rig: Rig = await rig_factory(lambda _: (status, {"Location": "https://169.254.169.254/"}))
    (nid,) = await rig.add()
    result = await rig.tick()
    row = await rig.row(nid)
    assert (result.dead, row.status, row.attempts, row.last_error_code) == (1, "dead", 1, code)
    assert len(rig.receiver.requests) == 1  # a redirect is not followed either


async def test_throttling_and_timeouts_are_retried(rig_factory: Any) -> None:
    rig: Rig = await rig_factory(lambda _: (429, {}))
    (nid,) = await rig.add()
    assert (await rig.tick()).retried == 1
    assert (await rig.row(nid)).status == "pending"


async def test_a_name_that_now_resolves_inward_is_dead_with_blocked_address_and_nothing_connects(
    rig_factory: Any,
) -> None:
    rig: Rig = await rig_factory()
    state = {"answer": PUBLIC_IP}

    async def moving(host: str, port: int) -> list[str]:
        return [state["answer"]]

    rig.registry = build_registry(rig.settings, rig.receiver.sender(resolver=moving))
    (first,) = await rig.add()
    assert (await rig.tick()).delivered == 1  # public when saved
    state["answer"] = "169.254.169.254"  # re-pointed at the metadata service
    (second,) = await rig.add(at=T0 + timedelta(minutes=1))
    await rig.tick(T0 + timedelta(minutes=1))
    row = await rig.row(second)
    assert (row.status, row.last_error_code, row.attempts) == ("dead", "blocked_address", 1)
    assert len(rig.receiver.requests) == 1
    assert (await rig.row(first)).status == "delivered"


async def test_a_destination_saved_before_the_ports_were_tightened_is_dead_with_blocked_port(
    rig_factory: Any,
) -> None:
    rig: Rig = await rig_factory()
    # The receiver listens on a random port, which the default list (443) does not contain: the
    # same state as an 8443 destination saved while the operator still allowed 8443.
    rig.registry = build_registry(rig.settings, rig.receiver.sender(allowed_ports=frozenset({443})))
    (nid,) = await rig.add()
    result = await rig.tick()
    row = await rig.row(nid)
    assert (result.dead, row.status, row.last_error_code, row.attempts) == (
        1,
        "dead",
        "blocked_port",
        1,
    )
    assert rig.receiver.connections == 0 and rig.receiver.requests == []


async def test_an_unreadable_secret_is_dead_with_config_unreadable_and_sends_nothing(
    rig_factory: Any,
) -> None:
    rig: Rig = await rig_factory()
    (nid,) = await rig.add()
    async with rig.api.engine.begin() as conn:
        await conn.execute(
            text("UPDATE alert_destinations SET config_encrypted = :b WHERE id = :i"),
            {"b": b"\x01" + b"\x00" * 60, "i": rig.destination_id},
        )
    await rig.tick()
    row = await rig.row(nid)
    assert (row.status, row.last_error_code) == ("dead", "config_unreadable")
    assert rig.receiver.connections == 0


async def test_a_ciphertext_moved_to_another_destination_does_not_open(rig_factory: Any) -> None:
    rig: Rig = await rig_factory()
    other = await seed_destination(
        rig.api.engine, rig.tenant, name="other", url="https://example.org/h"
    )
    async with rig.api.engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE alert_destinations SET config_encrypted = "
                "(SELECT config_encrypted FROM alert_destinations WHERE id = :o) WHERE id = :i"
            ),
            {"o": other, "i": rig.destination_id},
        )
    (nid,) = await rig.add()
    await rig.tick()
    assert (await rig.row(nid)).last_error_code == "config_unreadable"
    assert rig.receiver.connections == 0


# ---------------------------------------------------------------- health


async def test_consecutive_failures_flag_then_pause_the_destination_and_the_pause_is_audited(
    rig_factory: Any,
) -> None:
    rig: Rig = await rig_factory(
        lambda _: (400, {}), alerts_failing_after=2, alerts_pause_after_failures=3
    )
    states = []
    for i in range(3):
        await rig.add(at=T0 + timedelta(minutes=i))
        await rig.tick(T0 + timedelta(minutes=i))
        d = await rig.destination()
        states.append((d.state, d.consecutive_failures, d.paused_reason))
    assert states == [("active", 1, None), ("failing", 2, None), ("paused", 3, "failures")]
    async with rig.api.engine.connect() as conn:
        audit = (
            await conn.execute(
                text("SELECT * FROM audit_log WHERE action LIKE 'alert.destination.%'")
            )
        ).all()
    (entry,) = audit
    assert (entry.actor_kind, entry.actor_id, entry.action) == (
        "system", "alert-delivery", "alert.destination.auto_pause",
    )  # fmt: skip
    assert entry.details == {"reason": "failures", "consecutive_failures": 3}
    assert rig.receiver.requests and len(rig.receiver.requests) == 3
    # From now on, waiting alerts are closed honestly and nothing reaches the receiver.
    (late,) = await rig.add(at=T0 + timedelta(minutes=10))
    result = await rig.tick(T0 + timedelta(minutes=10))
    row = await rig.row(late)
    assert (result.skipped, row.status, row.skip_reason) == (1, "skipped", "destination_paused")
    assert len(rig.receiver.requests) == 3


async def test_a_success_resets_the_count_and_recovers_a_failing_destination(
    rig_factory: Any,
) -> None:
    mode = {"status": 500}
    rig: Rig = await rig_factory(
        lambda _: (mode["status"], {}), alerts_failing_after=2, alerts_pause_after_failures=5,
        alerts_max_attempts=1,
    )  # fmt: skip
    for i in range(2):
        await rig.add(at=T0 + timedelta(minutes=i))
        await rig.tick(T0 + timedelta(minutes=i))
    assert (await rig.destination()).state == "failing"
    mode["status"] = 200
    await rig.add(at=T0 + timedelta(minutes=5))
    await rig.tick(T0 + timedelta(minutes=5))
    d = await rig.destination()
    assert (d.state, d.consecutive_failures, d.paused_reason) == ("active", 0, None)


async def test_a_paused_destination_stays_paused_when_an_in_flight_alert_succeeds(
    rig_factory: Any,
) -> None:
    rig: Rig = await rig_factory()
    async with rig.api.engine.begin() as conn:
        await DestinationRepository(conn, rig.tenant).update(
            rig.destination_id, updated_by="x", state="paused", paused_reason="manual"
        )
    await rig.add()
    await rig.tick()
    d = await rig.destination()
    assert (d.state, d.paused_reason) == ("paused", "manual") and rig.receiver.requests == []


async def test_a_configuration_failure_counts_toward_health(rig_factory: Any) -> None:
    rig: Rig = await rig_factory()
    async with rig.api.engine.begin() as conn:
        await conn.execute(
            text("UPDATE alert_destinations SET config_encrypted = :b"), {"b": b"\x01" * 60}
        )
    await rig.add()
    await rig.tick()
    assert (await rig.destination()).consecutive_failures == 1


# ---------------------------------------------------------------- storm bound, caps, re-checks


async def test_a_retry_storm_is_bounded_per_tick_and_per_destination(rig_factory: Any) -> None:
    rig: Rig = await rig_factory(lambda _: (503, {}), alerts_deliver_batch=20)
    ids = await rig.add(50)
    result = await rig.tick()
    assert result.claimed == 20  # the global per-tick bound
    assert len(rig.receiver.requests) == 1  # one request to a destination that just failed
    assert (result.retried, result.deferred) == (1, 19)
    states = [await rig.row(i) for i in ids]
    assert Counter(r.attempts for r in states) == {1: 1, 0: 49}  # deferred rows spent no attempt
    assert sum(r.next_attempt_at > T0 for r in states) == 20  # pushed out, not hammered next tick
    assert (await rig.destination()).consecutive_failures == 1  # one failure per tick, not 20
    again = await rig.tick(T0 + timedelta(seconds=1))
    assert again.claimed == 20 and len(rig.receiver.requests) == 2  # the 30 untouched rows
    long = await rig.tick(T0 + timedelta(days=1))
    assert len(rig.receiver.requests) == 3 and long.claimed <= 20


async def test_the_per_destination_rate_limit_closes_the_excess_as_skipped(
    rig_factory: Any,
) -> None:
    rig: Rig = await rig_factory(alerts_rate_per_minute=3)
    ids = await rig.add(5)
    result = await rig.tick()
    assert (result.delivered, result.skipped) == (3, 2)
    found = [await rig.row(i) for i in ids]
    assert Counter((r.status, r.skip_reason) for r in found) == {
        ("delivered", None): 3,
        ("skipped", "rate_limited"): 2,
    }
    assert len(rig.receiver.requests) == 3
    await rig.add(1, at=T0 + timedelta(minutes=2))  # a minute later the window is clear
    assert (await rig.tick(T0 + timedelta(minutes=2))).delivered == 1


async def test_the_daily_cap_counts_what_was_already_delivered_and_resets_after_a_day(
    rig_factory: Any,
) -> None:
    rig: Rig = await rig_factory(alerts_daily_cap=4)
    await rig.add(2, status="delivered", delivered_at=T0 - timedelta(hours=3))
    await rig.add(1, status="delivered", delivered_at=T0 - timedelta(hours=30))  # outside the day
    ids = await rig.add(5)
    result = await rig.tick()
    assert (result.delivered, result.skipped) == (2, 3)
    reasons = Counter([(await rig.row(i)).skip_reason for i in ids])
    assert reasons == {None: 2, "daily_cap": 3}
    await rig.add(1, at=T0 + timedelta(days=1))
    assert (await rig.tick(T0 + timedelta(days=1))).delivered == 1


async def test_destination_and_rule_are_rechecked_at_send_time(rig_factory: Any) -> None:
    rig: Rig = await rig_factory()
    gone = await seed_destination(
        rig.api.engine, rig.tenant, name="gone", url=rig.receiver.url("/other")
    )
    doomed_rule = await seed_rule(rig.api.engine, rig.tenant, [rig.destination_id], name="doomed")
    (dead_destination,) = await pending(rig.api, rig.rule_id, gone)
    (dead_rule,) = await pending(rig.api, doomed_rule, rig.destination_id)
    async with (
        rig.api.engine.begin() as conn
    ):  # the history rows keep their place, reference cleared
        await DestinationRepository(conn, rig.tenant).delete(gone)
        await RuleRepository(conn, rig.tenant).delete(doomed_rule)
    result = await rig.tick()
    assert result.skipped == 2 and rig.receiver.requests == []
    assert (await rig.row(dead_destination)).skip_reason == "destination_deleted"
    assert (await rig.row(dead_rule)).skip_reason == "rule_deleted"


async def test_workspaces_never_deliver_each_others_alerts(rig_factory: Any) -> None:
    rig: Rig = await rig_factory()
    api = rig.api
    foreign_destination = await seed_destination(
        api.engine, api.other.context, name="theirs", url=rig.receiver.url("/theirs")
    )
    foreign_rule = await seed_rule(api.engine, api.other.context, [foreign_destination])
    (theirs,) = await pending(api, foreign_rule, foreign_destination, tenant=api.other.context)
    (mine,) = await rig.add()
    await rig.tick()
    assert [r.path for r in rig.receiver.requests] == ["/hook/SECRETPATHTOKEN1234"]
    assert (await rig.row(theirs)).status == "pending" and (
        await rig.row(mine)
    ).status == "delivered"


async def test_two_ticks_at_once_never_send_the_same_alert_twice(rig_factory: Any) -> None:
    rig: Rig = await rig_factory(alerts_deliver_batch=12)
    await rig.add(12)

    async def one() -> Any:
        return await rig.tick()

    results = await asyncio.gather(one(), one(), one())
    assert sum(r.delivered for r in results) == 12
    seen = ids_seen(rig.receiver)
    assert len(seen) == 12 and len(set(seen)) == 12  # each alert exactly once


# ---------------------------------------------------------------- pruning


async def test_pruning_deletes_only_old_finished_rows_in_bounded_batches(rig_factory: Any) -> None:
    rig: Rig = await rig_factory()
    old = T0 - timedelta(days=40)
    keep = []
    for status, reason in (("delivered", None), ("dead", None), ("skipped", "cooldown")):
        await rig.add(2, status=status, skip_reason=reason, at=old, created_at=old,
                      delivered_at=old if status == "delivered" else None)  # fmt: skip
    keep += await rig.add(1, at=old, created_at=old)  # old but still pending: never pruned
    keep += await rig.add(1, status="delivered", delivered_at=T0, created_at=T0)  # recent
    async with rig.api.engine.begin() as conn:
        repository = NotificationRepository(conn, rig.tenant)
        assert await repository.prune(T0 - timedelta(days=30), 4) == 4  # the batch bound
        assert await repository.prune(T0 - timedelta(days=30), 4) == 2
        assert await repository.prune(T0 - timedelta(days=30), 4) == 0
    left = await rows(rig.api)
    assert sorted(r.id for r in left) == sorted(keep)


async def test_the_prune_job_rearms_while_rows_remain_and_ends_when_none_do(
    rig_factory: Any,
) -> None:
    rig: Rig = await rig_factory()
    old = datetime.now(UTC) - timedelta(days=90)
    await rig.add(3, status="delivered", delivered_at=old, created_at=old, at=old)
    settings = dsettings(alerts_retention_days=30, alerts_prune_batch=2)
    token = set_job_settings(settings)
    try:
        job = Job(uuid.uuid4(), ALERT_PRUNE, rig.tenant.workspace_id, "k", {}, 1, "t")
        async with rig.api.engine.begin() as conn:
            await alert_prune(job, conn)
        assert await _jobs(rig, ALERT_PRUNE) == 1  # a full batch: carry on shortly
        async with rig.api.engine.begin() as conn:
            await conn.execute(text("DELETE FROM outbox_jobs"))
            await alert_prune(job, conn)
        assert await _jobs(rig, ALERT_PRUNE) == 0  # nothing left to wait for
        assert await rows(rig.api) == []
        await rig.add(1, at=datetime.now(UTC), created_at=datetime.now(UTC))
        async with rig.api.engine.begin() as conn:
            await alert_prune(job, conn)
        assert await _jobs(rig, ALERT_PRUNE) == 1  # a young row remains: look again later
    finally:
        reset_job_settings(token)


async def _jobs(rig: Rig, job_type: str) -> int:
    async with rig.api.engine.connect() as conn:
        return int(
            (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM outbox_jobs WHERE job_type = :t AND status='pending'"
                    ),
                    {"t": job_type},
                )
            ).scalar_one()
        )


# ---------------------------------------------------------------- jobs and the worker


async def test_the_delivery_hold_leaves_rows_pending(rig_factory: Any) -> None:
    rig: Rig = await rig_factory()
    (nid,) = await rig.add(at=datetime.now(UTC) - timedelta(minutes=1))
    token = set_job_settings(dsettings(alerts_delivery_enabled=False))
    try:
        job = Job(uuid.uuid4(), DELIVER_ALERTS, rig.tenant.workspace_id, "k", {}, 1, "t")
        async with rig.api.engine.begin() as conn:
            await deliver_alerts(job, conn)
    finally:
        reset_job_settings(token)
    assert (await rig.row(nid)).status == "pending" and rig.receiver.requests == []


async def test_the_job_asks_for_the_next_tick_when_rows_are_left_and_schedules_retries(
    rig_factory: Any,
) -> None:
    rig: Rig = await rig_factory(lambda _: (503, {}), alerts_deliver_batch=2)
    await rig.add(5, at=datetime.now(UTC) - timedelta(minutes=5))
    settings = rig.settings
    token = set_job_settings(settings)
    from abb_api.alerts.delivery import use_registry

    try:
        job = Job(uuid.uuid4(), DELIVER_ALERTS, rig.tenant.workspace_id, "k", {}, 1, "t")
        with use_registry(rig.registry):
            async with rig.api.engine.begin() as conn:
                await deliver_alerts(job, conn)
    finally:
        reset_job_settings(token)
    async with rig.api.engine.connect() as conn:
        jobs = (
            await conn.execute(
                text("SELECT dedupe_key, available_at FROM outbox_jobs WHERE job_type = :t"),
                {"t": DELIVER_ALERTS},
            )
        ).all()
    # Three rows are still due now (the batch was two), so the next tick is requested at once.
    assert [j.dedupe_key for j in jobs] == [str(rig.tenant.workspace_id)]


async def test_a_retry_job_is_moved_earlier_never_later(rig_factory: Any) -> None:
    from abb_api.alerts.schedule import schedule_retry

    rig: Rig = await rig_factory()
    async with rig.api.engine.begin() as conn:
        await schedule_retry(conn, rig.tenant, T0 + timedelta(hours=1))
        await schedule_retry(conn, rig.tenant, T0 + timedelta(minutes=5))
        await schedule_retry(conn, rig.tenant, T0 + timedelta(hours=2))
    async with rig.api.engine.connect() as conn:
        found = (await conn.execute(text("SELECT available_at FROM outbox_jobs"))).all()
    assert [r.available_at for r in found] == [T0 + timedelta(minutes=5)]


@pytest.fixture
async def worker(
    database_url: str, runtime_database_url: str, rig_factory: Any
) -> AsyncIterator[Any]:
    engines: list[AsyncEngine] = []

    def make(rig: Rig, settings: Settings) -> Worker:
        engine = create_async_engine(runtime_url(database_url), poolclass=NullPool)
        engines.append(engine)
        return Worker(engine, HANDLERS, settings, owner="delivery-test")

    yield make
    for engine in engines:
        await engine.dispose()


async def test_a_failed_run_is_delivered_end_to_end_by_the_worker_as_the_runtime_role(
    rig_factory: Any, worker: Any, caplog: pytest.LogCaptureFixture
) -> None:
    from abb_api.alerts.delivery import use_registry
    from tests.alerts_helpers import post_run

    caplog.set_level(logging.DEBUG)
    rig: Rig = await rig_factory()
    settings = dsettings(summary_debounce_seconds=0.0, findings_delay_seconds=0.0)
    rig.settings = settings
    rig.registry = build_registry(settings, rig.receiver.sender())
    await seed_rule(rig.api.engine, rig.tenant, [rig.destination_id], name="fails")
    w: Worker = worker(rig, settings)
    run_id = await post_run(rig.api, "FAILED")
    with use_registry(rig.registry):
        for _ in range(60):
            if await w.run_once() == 0 and rig.receiver.requests:
                break
    assert run_id
    (first, *_) = rig.receiver.requests
    body = json.loads(first.body)
    assert body["kind"] == "run_failed" and body["subject"]["run_id"] == run_id
    assert first.headers["x-abb-alert-id"] == body["alert_id"]
    found = [r for r in await rows(rig.api) if r.status == "delivered"]
    assert found and all(r.attempts == 1 for r in found)
    # Two rules (the rig's and this test's) on one destination: two alerts, one request each.
    assert len(set(ids_seen(rig.receiver))) == len(rig.receiver.requests)
    assert SENTINEL_SIGNING not in caplog.text and "SECRETPATHTOKEN1234" not in caplog.text


async def test_the_worker_retries_after_the_delay_through_the_retry_job(
    rig_factory: Any, worker: Any
) -> None:
    from abb_api.alerts.delivery import use_registry

    calls = {"n": 0}

    def flaky(_: Request) -> tuple[int, dict[str, str]]:
        calls["n"] += 1
        return (500, {}) if calls["n"] == 1 else (200, {})

    rig: Rig = await rig_factory(flaky, alerts_backoff_base_seconds=0.0)
    settings = rig.settings
    (nid,) = await rig.add(at=datetime.now(UTC) - timedelta(seconds=5))
    async with rig.api.engine.begin() as conn:
        await request_delivery(OutboxRepository(conn, rig.tenant), rig.tenant)
    w: Worker = worker(rig, settings)
    with use_registry(rig.registry):
        await w.run_once()
        assert (await rig.row(nid)).status == "pending" and calls["n"] == 1
        await asyncio.sleep(1.3)  # the retry job is scheduled a second after the due time
        for _ in range(5):
            await w.run_once()
    row = await rig.row(nid)
    assert (row.status, row.attempts) == ("delivered", 2)
    assert len(set(ids_seen(rig.receiver))) == 1


# ---------------------------------------------------------------- content and secrets


async def test_no_planted_content_or_secret_reaches_a_receiver_a_mailbox_a_log_or_a_row(
    rig_factory: Any, worker: Any, caplog: pytest.LogCaptureFixture
) -> None:
    from abb_api.alerts.delivery import use_registry
    from tests.alerts_helpers import run_events
    from tests.security_helpers import PLANT, planted_run

    caplog.set_level(logging.DEBUG)
    async with SmtpSink() as sink:
        rig: Rig = await rig_factory()
        settings = dsettings(
            summary_debounce_seconds=0.0,
            findings_delay_seconds=0.0,
            smtp_host="127.0.0.1",
            smtp_port=sink.port,
            smtp_from="abb@example.com",
            smtp_starttls=False,
        )
        rig.settings = settings
        rig.registry = build_registry(settings, rig.receiver.sender())
        slack_receiver = TlsReceiver()
        await slack_receiver.__aenter__()
        slack = await seed_destination(
            rig.api.engine, rig.tenant, name="slack", dest_type="slack",
            url=slack_receiver.url("/services/T0/B0/SLACKTOKENSENTINEL"),
        )  # fmt: skip
        mail = await seed_destination(
            rig.api.engine, rig.tenant, name="mail", dest_type="email",
            recipients=["secret.person@example.com"],
        )  # fmt: skip
        both = build_registry(settings, slack_receiver.sender())
        rig.registry = {**both, "webhook": rig.registry["webhook"]}
        for trigger, filters in (
            (m.RUN_FAILED, {}),
            (m.SECURITY_FINDING, {"min_severity": "info"}),
        ):
            await seed_rule(
                rig.api.engine, rig.tenant, [rig.destination_id, slack, mail],
                name=f"r-{trigger}", trigger=trigger, filters=filters,
            )  # fmt: skip
        w: Worker = worker(rig, settings)
        for kind in ("FAILED", "TIMED_OUT"):
            run_id, events = run_events(kind, message=f"{PLANT} fatal /home/{PLANT}/.ssh")
            events[0]["attributes"] = {"run.name": f"{PLANT}-name"}
            assert (await rig.api.post_batch(events)).status_code == 202
            assert run_id
        run, _ = planted_run("acme")
        assert (await rig.api.post_batch(run.wires)).status_code == 202
        with use_registry(rig.registry):
            for _ in range(80):
                await w.run_once()
        await slack_receiver.__aexit__()

    assert rig.receiver.requests and slack_receiver.requests and sink.messages
    seen = [r.body.decode() for r in rig.receiver.requests + slack_receiver.requests]
    seen += [msg.decode(errors="replace") for msg in sink.messages]
    for blob in seen:
        assert PLANT not in blob
    secrets = (SENTINEL_SIGNING, "SECRETPATHTOKEN1234", "SLACKTOKENSENTINEL", "secret.person")
    for secret in secrets:
        assert secret not in caplog.text, secret
        assert not any(secret in blob for blob in seen if secret == SENTINEL_SIGNING)
    async with rig.api.engine.connect() as conn:
        for query in (
            "SELECT t::text FROM alert_notifications t",
            "SELECT t::text FROM audit_log t",
            "SELECT t::text FROM outbox_jobs t WHERE job_type LIKE '%alert%'",
        ):
            for (blob,) in await conn.execute(text(query)):
                assert PLANT not in blob
                for secret in secrets:
                    assert secret not in blob
    for role in ("OWNER", "VIEWER", "SECURITY"):
        headers = await person(rig.api, f"{role.lower()}@acme.test", role)
        listing = await rig.api.client.get("/v1/alerts/notifications?limit=200", headers=headers)
        assert listing.status_code == 200
        assert "delivered" in listing.text
        for forbidden in (PLANT, *secrets, RESPONSE_SENTINEL):
            assert forbidden not in listing.text, (role, forbidden)
    assert PLANT not in caplog.text


async def test_failure_logs_carry_codes_only_never_urls_secrets_or_bodies(
    rig_factory: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rig: Rig = await rig_factory(lambda _: (500, {}), alerts_max_attempts=1)
    await rig.add(3)
    await rig.tick()
    assert "dead-lettered" in caplog.text
    for forbidden in (
        SENTINEL_SIGNING, "SECRETPATHTOKEN1234", HOST, RESPONSE_SENTINEL, "x-abb-signature",
    ):  # fmt: skip
        assert forbidden.lower() not in caplog.text.lower(), forbidden


async def test_a_tick_woken_before_its_rows_are_due_schedules_itself_instead_of_ending_the_chain(
    rig_factory: Any,
) -> None:
    from abb_api.alerts.delivery import use_registry

    rig: Rig = await rig_factory()
    later = datetime.now(UTC) + timedelta(minutes=10)
    await rig.add(at=later)
    token = set_job_settings(rig.settings)
    try:
        job = Job(uuid.uuid4(), DELIVER_ALERTS, rig.tenant.workspace_id, "k", {}, 1, "t")
        with use_registry(rig.registry):
            async with rig.api.engine.begin() as conn:
                await deliver_alerts(job, conn)
    finally:
        reset_job_settings(token)
    async with rig.api.engine.connect() as conn:
        found = (await conn.execute(text("SELECT dedupe_key, available_at FROM outbox_jobs"))).all()
    assert [j.dedupe_key for j in found] == [f"{rig.tenant.workspace_id}:retry"]
    assert abs((found[0].available_at - later).total_seconds() - 1) < 1
    assert rig.receiver.requests == []
