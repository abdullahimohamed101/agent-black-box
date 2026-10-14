"""Fixes from the Phase 17 adversarial review that need their own scenario (the evaluation ones
live in test_alerts_evaluation.py, the sweep chain in test_alerts_sweep_chain.py)."""

import asyncio
import contextlib
import time
from collections import Counter
from collections.abc import AsyncIterator
from typing import Any

import pytest
from abb_event_schema.ids import IdKind
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.alerts.delivery import deliver_due, use_registry
from abb_api.alerts.model import MAX_COOLDOWN_SECONDS
from abb_api.alerts.notifiers import SendResult
from abb_api.alerts.repository import DestinationRepository, NotificationRepository
from abb_api.core.config import Settings
from abb_api.ids import public_id
from tests.alerts_helpers import seed_destination, seed_rule
from tests.api_fixtures import WEB_ORIGIN, Api, build_api, person
from tests.conftest import make_settings
from tests.test_alerts_delivery import T0, dsettings, pending


class _Ok:
    async def send(self, destination: object, alert: object) -> SendResult:
        return SendResult(True)


async def test_concurrent_ticks_apply_destination_health_in_one_order_and_do_not_deadlock(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review P2-5: the tick of `<ws>` and the tick of `<ws>:retry` can overlap. Each locks the
    destinations it touched one by one; in dictionary order two ticks that touched A,B and B,A
    deadlock."""
    tenant = api.tenant.context
    a = await seed_destination(api.engine, tenant, name="a")
    b = await seed_destination(api.engine, tenant, name="b", url="https://example.org/h")
    rule = await seed_rule(api.engine, tenant, [a, b])
    from datetime import timedelta

    for offset, destination in enumerate((a, b, b, a)):  # tick 1 gets A,B and tick 2 gets B,A
        await pending(api, rule, destination, at=T0 + timedelta(seconds=offset))
    settings = dsettings(alerts_deliver_batch=2)
    registry = {"webhook": _Ok(), "slack": _Ok(), "email": _Ok()}

    real = DestinationRepository.apply_health
    first_done: Counter[int] = Counter()
    both_locked_one = asyncio.Event()

    async def gated(self: DestinationRepository, destination_id: Any, **kwargs: Any) -> Any:
        out = await real(self, destination_id, **kwargs)
        key = id(self._conn)
        first_done[key] += 1
        if first_done[key] == 1:
            if len(first_done) == 2:
                both_locked_one.set()
            # Let the other tick take its first lock too; with one lock order it queues behind this
            # tick instead, and this wait simply times out.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(both_locked_one.wait(), 0.8)
        return out

    monkeypatch.setattr(DestinationRepository, "apply_health", gated)

    async def tick() -> Any:
        async with api.engine.begin() as conn:
            return await deliver_due(
                conn,
                tenant,
                now=T0 + timedelta(minutes=1),
                settings=settings,
                registry=registry,
            )

    first = asyncio.create_task(tick())
    await asyncio.sleep(0.4)  # the first tick has claimed its two rows
    second = asyncio.create_task(tick())
    results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 30)
    assert not [r for r in results if isinstance(r, DBAPIError)], results
    assert sorted(r.delivered for r in results if not isinstance(r, BaseException)) == [2, 2]


# ---------------------------------------------------------------- P2-1 locks


async def test_closing_waiting_alerts_does_not_queue_behind_a_tick_that_holds_a_row(
    api: Api,
) -> None:
    tenant = api.tenant.context
    destination = await seed_destination(api.engine, tenant)
    rule = await seed_rule(api.engine, tenant, [destination])
    await pending(api, rule, destination, n=2, at=T0)
    async with api.engine.connect() as ticking:
        await ticking.begin()
        held = await NotificationRepository(ticking, tenant).claim_due(T0, 1)  # the tick's batch
        assert len(held) == 1
        async with api.engine.begin() as owner:
            closed = await asyncio.wait_for(
                NotificationRepository(owner, tenant).skip_pending_of_destination(
                    destination, "destination_deleted"
                ),
                5,
            )
            other = await asyncio.wait_for(
                NotificationRepository(owner, tenant).skip_pending_of_rule(rule, "rule_deleted"),
                5,
            )
        await ticking.rollback()
    assert (closed, other) == (1, 0)  # the held row is the tick's to close at send time


@pytest.fixture
async def impatient(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[Api]:
    settings = make_settings(database_url, web_origin=WEB_ORIGIN, alerts_lock_timeout_seconds=0.5)
    async for instance in build_api(database_url, engine, settings=settings):
        yield instance


async def test_a_delete_blocked_by_a_running_tick_fails_fast_with_a_fixed_code(
    impatient: Api,
) -> None:
    api = impatient
    tenant = api.tenant.context
    destination = await seed_destination(api.engine, tenant)
    rule = await seed_rule(api.engine, tenant, [destination])
    await pending(api, rule, destination, n=1, at=T0)
    headers = await person(api, "owner@acme.test", "OWNER")
    path = f"/v1/alerts/destinations/{public_id(IdKind.DESTINATION, destination)}"
    async with api.engine.connect() as ticking:
        await ticking.begin()
        await NotificationRepository(ticking, tenant).claim_due(T0, 5)
        started = time.monotonic()
        blocked = await asyncio.wait_for(api.client.delete(path, headers=headers), 10)
        waited = time.monotonic() - started
        assert blocked.status_code == 409, blocked.text
        assert (
            blocked.json()["error"]["code"] == "ALERT_BUSY" and blocked.json()["error"]["retryable"]
        )
        assert waited < 5
        await ticking.rollback()
    # nothing was half done, and the same request succeeds once the tick is over
    assert (await api.client.get(path, headers=headers)).status_code == 200
    assert (await api.client.delete(path, headers=headers)).status_code == 204


# ---------------------------------------------------------------- P3-5 send-test budget


@pytest.fixture
async def small_budget(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[Api]:
    settings = make_settings(
        database_url, web_origin=WEB_ORIGIN, alerts_writes_per_minute=3, alerts_tests_per_minute=50
    )
    async for instance in build_api(database_url, engine, settings=settings):
        yield instance


async def test_a_send_test_costs_one_write_token_not_two(small_budget: Api) -> None:
    api = small_budget
    destination = await seed_destination(api.engine, api.tenant.context)
    headers = await person(api, "owner@acme.test", "OWNER")
    path = f"/v1/alerts/destinations/{public_id(IdKind.DESTINATION, destination)}/test"
    with use_registry({"webhook": _Ok(), "slack": _Ok(), "email": _Ok()}):
        statuses = [(await api.client.post(path, headers=headers)).status_code for _ in range(4)]
    assert statuses == [200, 200, 200, 429]


# ---------------------------------------------------------------- P3-4 / P2-2 settings


def test_history_must_outlive_the_longest_cooldown() -> None:
    url = "postgresql+asyncpg://x:y@localhost/z"
    make_settings(url, alerts_retention_days=7)
    with pytest.raises(ValueError, match="alerts_retention_days"):
        make_settings(url, alerts_retention_days=6)
    assert MAX_COOLDOWN_SECONDS <= 7 * 86400  # the validator's constant follows the model's


def test_a_tick_is_a_small_batch_by_default() -> None:
    assert Settings(database_url="postgresql+asyncpg://x:y@localhost/z").alerts_deliver_batch == 5


# ---------------------------------------------------------------- P2-1 email wait


async def test_one_email_never_holds_a_tick_longer_than_the_smtp_timeout() -> None:
    """A relay that keeps trickling bytes never trips the socket timeout; the whole send is bounded
    by one SMTP timeout (it used to be two), like the HTTPS channels' single deadline."""
    from abb_api.alerts import notifiers as n
    from tests.test_alerts_notifiers import alert, email_destination

    async def trickle(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                writer.write(b"2")  # a banner that never ends
                await writer.drain()
                await asyncio.sleep(0.2)
        except (ConnectionError, asyncio.CancelledError):
            pass

    server = await asyncio.start_server(trickle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    settings = make_settings(
        "postgresql+asyncpg://x:y@localhost/z",
        smtp_host="127.0.0.1",
        smtp_port=port,
        smtp_from="abb@example.com",
        smtp_starttls=False,
        alerts_smtp_timeout_seconds=1,
    )
    try:
        started = time.monotonic()
        result = await n.EmailNotifier(settings).send(email_destination(), alert())
        waited = time.monotonic() - started
    finally:
        server.close()
    assert (result.ok, result.error_code) == (False, "timeout")
    assert 0.8 < waited < 1.6, waited
