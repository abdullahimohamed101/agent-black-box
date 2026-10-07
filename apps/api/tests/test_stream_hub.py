"""Commit-time NOTIFY and the listener hub, against real PostgreSQL (ADR-022)."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from abb_event_schema.ids import to_uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.ingestion.store import PgEventStore
from abb_api.streaming import notify
from abb_api.streaming.hub import APPLICATION_NAME, StreamHub
from tests.ingest_helpers import Tenant, build_event, make_run_ids, make_tenant

FAST = {"reconnect_min_seconds": 0.05, "reconnect_max_seconds": 0.2, "heartbeat_seconds": 0.2}


@pytest.fixture
async def hub(runtime_database_url: str) -> AsyncIterator[StreamHub]:
    """A hub connected as the restricted runtime role: LISTEN/NOTIFY must not need more."""
    h = StreamHub(runtime_database_url, **FAST)
    h.start()
    assert await h.wait_connected(5)
    yield h
    await h.stop()


def key_of(tenant: Tenant, run: dict[str, str]) -> notify.StreamKey:
    return (tenant.context.workspace_id, to_uuid(run["run_id"]))


async def ingest_one(engine: AsyncEngine, tenant: Tenant, run: dict[str, str], n: int = 1):  # type: ignore[no-untyped-def]
    event = build_event(tenant, run, n=n)
    async with engine.begin() as conn:
        await PgEventStore(conn, tenant.context).ingest([event])
    return event


# ------------------------------------------------------------------ payload


def test_payloads_round_trip_and_garbage_is_ignored() -> None:
    key = (uuid.uuid4(), uuid.uuid4())
    assert notify.decode(notify.encode(key)) == key
    for junk in [
        "",
        "x",
        "a:b",
        f"{key[0]}",
        f"{key[0]}:{key[1]}:extra",
        f"{key[0]}:{str(key[1]).upper()}",
    ]:
        assert notify.decode(junk) is None


# ------------------------------------------------------------------ wake-ups


async def test_a_committed_ingest_wakes_that_runs_subscribers_only(
    engine: AsyncEngine, hub: StreamHub
) -> None:
    tenant, other = await make_tenant(engine, "hub1"), await make_tenant(engine, "hub2")
    run, other_run = make_run_ids(), make_run_ids()
    mine, theirs = hub.subscribe(key_of(tenant, run)), hub.subscribe(key_of(other, other_run))
    await ingest_one(engine, tenant, run)
    assert await mine.wait(5) is True
    assert await theirs.wait(0.3) is False


async def test_a_rolled_back_or_duplicate_ingest_wakes_nobody(
    engine: AsyncEngine, hub: StreamHub
) -> None:
    tenant = await make_tenant(engine, "hub3")
    run = make_run_ids()
    sub = hub.subscribe(key_of(tenant, run))
    event = build_event(tenant, run, n=1)
    with pytest.raises(RuntimeError):
        async with engine.begin() as conn:
            await PgEventStore(conn, tenant.context).ingest([event])
            raise RuntimeError("abort after ingest")
    assert await sub.wait(0.4) is False
    async with engine.begin() as conn:
        await PgEventStore(conn, tenant.context).ingest([event])
    assert await sub.wait(5) is True
    async with engine.begin() as conn:  # the same event again: nothing new, nobody woken
        await PgEventStore(conn, tenant.context).ingest([event])
    assert await sub.wait(0.4) is False


async def test_many_notifications_coalesce_into_one_wake(
    engine: AsyncEngine, hub: StreamHub
) -> None:
    tenant = await make_tenant(engine, "hub4")
    run = make_run_ids()
    sub = hub.subscribe(key_of(tenant, run))
    for n in range(1, 6):
        await ingest_one(engine, tenant, run, n)
    await asyncio.sleep(0.3)
    assert await sub.wait(1) is True
    assert await sub.wait(0.3) is False


async def test_a_notification_landing_while_the_caller_works_is_not_lost(
    engine: AsyncEngine, hub: StreamHub
) -> None:
    tenant = await make_tenant(engine, "hub5")
    run = make_run_ids()
    sub = hub.subscribe(key_of(tenant, run))
    await ingest_one(engine, tenant, run, 1)
    assert await sub.wait(5) is True  # the caller now "queries"...
    await ingest_one(engine, tenant, run, 2)  # ...and more arrives meanwhile
    await asyncio.sleep(0.3)
    assert await sub.wait(1) is True


async def test_every_process_hears_one_commit(
    engine: AsyncEngine, runtime_database_url: str
) -> None:
    """Two hubs stand in for two API processes."""
    tenant = await make_tenant(engine, "hub6")
    run = make_run_ids()
    first, second = StreamHub(runtime_database_url, **FAST), StreamHub(runtime_database_url, **FAST)
    for h in (first, second):
        h.start()
        assert await h.wait_connected(5)
    try:
        a, b = first.subscribe(key_of(tenant, run)), second.subscribe(key_of(tenant, run))
        await ingest_one(engine, tenant, run)
        assert tuple(await asyncio.gather(a.wait(5), b.wait(5))) == (True, True)
    finally:
        await asyncio.gather(first.stop(), second.stop())


async def test_unsubscribing_releases_the_subscription(hub: StreamHub) -> None:
    sub = hub.subscribe((uuid.uuid4(), uuid.uuid4()))
    assert hub.subscriber_count == 1
    sub.close()
    sub.close()  # idempotent
    assert hub.subscriber_count == 0


# ------------------------------------------------------------------ failure


async def terminate_listener(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE application_name = :name AND pid <> pg_backend_pid()"
            ),
            {"name": APPLICATION_NAME},
        )
        return len(result.all())


async def test_a_killed_listener_reconnects_and_wakes_everyone(
    engine: AsyncEngine, hub: StreamHub
) -> None:
    tenant = await make_tenant(engine, "hub7")
    run = make_run_ids()
    sub = hub.subscribe(key_of(tenant, run))
    assert await terminate_listener(engine) >= 1
    assert (
        await sub.wait(10) is True
    )  # reconnect wakes everyone: notifications may have been missed
    assert await hub.wait_connected(10)
    await ingest_one(engine, tenant, run)
    assert await sub.wait(5) is True  # and live delivery works again


async def test_an_unreachable_database_never_raises_and_subscribers_just_time_out() -> None:
    hub = StreamHub("postgresql+asyncpg://nobody:x@127.0.0.1:1/none", **FAST)
    hub.start()
    sub = hub.subscribe((uuid.uuid4(), uuid.uuid4()))
    assert await sub.wait(0.3) is False
    assert hub.connected is False
    await asyncio.wait_for(hub.stop(), 2)


async def test_stopping_closes_the_listener_connection(
    engine: AsyncEngine, runtime_database_url: str
) -> None:
    h = StreamHub(runtime_database_url, **FAST)
    h.start()
    assert await h.wait_connected(5)
    await h.stop()
    async with engine.connect() as conn:
        count = (
            await conn.execute(
                text("SELECT count(*) FROM pg_stat_activity WHERE application_name = :n"),
                {"n": APPLICATION_NAME},
            )
        ).scalar_one()
    assert count == 0


async def test_the_runtime_role_can_listen_and_notify(runtime_database_url: str) -> None:
    from sqlalchemy.engine import make_url

    dsn = make_url(runtime_database_url).set(drivername="postgresql").render_as_string(False)
    conn = await asyncpg.connect(dsn)
    got: list[str] = []
    try:
        await conn.add_listener(notify.CHANNEL, lambda *args: got.append(args[3]))
        await conn.execute("SELECT pg_notify($1, $2)", notify.CHANNEL, "ping")
        await asyncio.sleep(0.2)
    finally:
        await conn.close()
    assert got == ["ping"]
