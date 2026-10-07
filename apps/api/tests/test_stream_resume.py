"""Arrival-time resume: the cursor's overlap logic and the queries behind it, on real PostgreSQL."""

import uuid
from datetime import timedelta

from abb_event_schema.event import Event
from abb_event_schema.ids import to_uuid
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.ingestion.store import PgEventStore
from abb_api.runs.event_queries import EventQueries
from abb_api.streaming.resume import ArrivalCursor
from tests.ingest_helpers import RECEIVED, Tenant, build_event, make_run_ids, make_tenant

OVERLAP = timedelta(seconds=30)


def arrives(tenant: Tenant, run: dict[str, str], n: int, seconds: float) -> Event:
    """Event `n` received `seconds` after the base time."""
    event = build_event(tenant, run, n=n)
    return event.model_copy(update={"received_at": RECEIVED + timedelta(seconds=seconds)})


def rows(*events: Event) -> list[tuple[Event, bool]]:
    return [(e, False) for e in events]


async def ingest(engine: AsyncEngine, tenant: Tenant, events: list[Event]) -> None:
    async with engine.begin() as conn:
        await PgEventStore(conn, tenant.context).ingest(events)


# ------------------------------------------------------------------ cursor (pure)


async def test_a_fresh_cursor_reads_from_the_start_and_sends_each_event_once(
    engine: AsyncEngine,
) -> None:
    tenant = await make_tenant(engine, "cur1")
    run = make_run_ids()
    a, b = arrives(tenant, run, 1, 0), arrives(tenant, run, 2, 1)
    cursor = ArrivalCursor(None, OVERLAP)
    assert cursor.lower_bound is None
    assert [e.event_id for e, _ in cursor.unseen(rows(a, b))] == [a.event_id, b.event_id]
    assert cursor.lower_bound == b.received_at - OVERLAP
    assert cursor.unseen(rows(a, b)) == []  # the overlap re-read sends nothing new


def test_a_cursor_resumed_from_a_watermark_starts_one_overlap_earlier() -> None:
    watermark = RECEIVED
    assert ArrivalCursor(watermark, OVERLAP).lower_bound == watermark - OVERLAP


async def test_a_late_committed_event_with_an_older_arrival_time_is_still_sent(
    engine: AsyncEngine,
) -> None:
    tenant = await make_tenant(engine, "cur2")
    run = make_run_ids()
    newer, late = (
        arrives(tenant, run, 2, 10),
        arrives(tenant, run, 1, 5),
    )  # `late` commits after `newer`
    cursor = ArrivalCursor(None, OVERLAP)
    cursor.unseen(rows(newer))
    assert [e.event_id for e, _ in cursor.unseen(rows(late, newer))] == [late.event_id]


async def test_sent_memory_is_pruned_to_the_overlap_window(engine: AsyncEngine) -> None:
    tenant = await make_tenant(engine, "cur3")
    run = make_run_ids()
    old, new = arrives(tenant, run, 1, 0), arrives(tenant, run, 2, 600)
    cursor = ArrivalCursor(None, OVERLAP)
    cursor.unseen(rows(old))
    cursor.unseen(rows(new))
    assert set(cursor._sent) == {to_uuid(new.event_id)}  # bounded memory is the point


def test_a_negative_overlap_is_rejected() -> None:
    try:
        ArrivalCursor(None, timedelta(seconds=-1))
    except ValueError:
        return
    raise AssertionError("expected ValueError")


# ------------------------------------------------------------------ queries


async def test_arrival_of_returns_the_receive_time_and_is_tenant_and_run_scoped(
    engine: AsyncEngine,
) -> None:
    tenant, other = await make_tenant(engine, "arr1"), await make_tenant(engine, "arr2")
    run, other_run = make_run_ids(), make_run_ids()
    event = arrives(tenant, run, 1, 3)
    await ingest(engine, tenant, [event])
    async with engine.connect() as conn:
        mine = EventQueries(conn, tenant.context)
        assert (
            await mine.arrival_of(to_uuid(run["run_id"]), to_uuid(event.event_id))
            == event.received_at
        )
        assert await mine.arrival_of(to_uuid(other_run["run_id"]), to_uuid(event.event_id)) is None
        assert await mine.arrival_of(to_uuid(run["run_id"]), uuid.uuid4()) is None
        theirs = EventQueries(conn, other.context)
        assert await theirs.arrival_of(to_uuid(run["run_id"]), to_uuid(event.event_id)) is None


async def test_arrived_since_is_in_arrival_order_inclusive_and_pageable(
    engine: AsyncEngine,
) -> None:
    tenant = await make_tenant(engine, "arr3")
    run = make_run_ids()
    events = [arrives(tenant, run, n, seconds) for n, seconds in [(1, 5), (2, 1), (3, 3), (4, 3)]]
    await ingest(engine, tenant, events)
    run_id = to_uuid(run["run_id"])
    expected = sorted(events, key=lambda e: (e.received_at, to_uuid(e.event_id).bytes))
    async with engine.connect() as conn:
        queries = EventQueries(conn, tenant.context)
        everything = await queries.arrived_since(run_id, since=None, limit=10)
        assert [e.event_id for e, _ in everything] == [e.event_id for e in expected]
        # two events share the 3 s arrival time: the inclusive bound returns both and the later one
        from_three = await queries.arrived_since(run_id, since=expected[1].received_at, limit=10)
        assert {e.event_id for e, _ in from_three} == {e.event_id for e in expected[1:]}
        first = await queries.arrived_since(run_id, since=None, limit=2)
        last = first[-1][0]
        rest = await queries.arrived_since(
            run_id, since=None, after=(last.received_at, to_uuid(last.event_id)), limit=10
        )
        assert [e.event_id for e, _ in first + rest] == [
            e.event_id for e in expected
        ]  # no gaps, no repeats


async def test_arrived_since_never_returns_another_runs_or_tenants_events(
    engine: AsyncEngine,
) -> None:
    tenant, other = await make_tenant(engine, "arr4"), await make_tenant(engine, "arr5")
    run, other_run = make_run_ids(), make_run_ids()
    mine, theirs, same_tenant_other_run = (
        arrives(tenant, run, 1, 0),
        arrives(other, make_run_ids(), 1, 0),
        arrives(tenant, other_run, 1, 0),
    )
    await ingest(engine, tenant, [mine, same_tenant_other_run])
    await ingest(engine, other, [theirs])
    async with engine.connect() as conn:
        got = await EventQueries(conn, tenant.context).arrived_since(
            to_uuid(run["run_id"]), since=None, limit=10
        )
        assert [e.event_id for e, _ in got] == [mine.event_id]


async def test_a_resume_with_overlap_finds_an_event_that_became_visible_late(
    engine: AsyncEngine,
) -> None:
    """Why the overlap exists: `late` has an older arrival time but commits after `newer`."""
    tenant = await make_tenant(engine, "arr6")
    run = make_run_ids()
    newer, late = arrives(tenant, run, 2, 10), arrives(tenant, run, 1, 5)
    run_id = to_uuid(run["run_id"])
    await ingest(engine, tenant, [newer])
    cursor = ArrivalCursor(newer.received_at, OVERLAP)  # a client reconnecting after `newer`
    await ingest(engine, tenant, [late])
    async with engine.connect() as conn:
        got = await EventQueries(conn, tenant.context).arrived_since(
            run_id, since=cursor.lower_bound, limit=10
        )
    assert late.event_id in {e.event_id for e, _ in got}


async def test_a_transaction_older_than_the_overlap_is_missed_and_that_is_the_documented_limit(
    engine: AsyncEngine,
) -> None:
    tenant = await make_tenant(engine, "arr7")
    run = make_run_ids()
    newer, stale = (
        arrives(tenant, run, 2, 100),
        arrives(tenant, run, 1, 5),
    )  # 95 s older than `newer`
    await ingest(engine, tenant, [newer, stale])
    cursor = ArrivalCursor(newer.received_at, OVERLAP)
    async with engine.connect() as conn:
        got = await EventQueries(conn, tenant.context).arrived_since(
            to_uuid(run["run_id"]), since=cursor.lower_bound, limit=10
        )
    assert stale.event_id not in {e.event_id for e, _ in got}
