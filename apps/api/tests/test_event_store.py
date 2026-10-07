"""The ingest transaction against a real PostgreSQL: idempotency, conflicts, tenancy, concurrency."""

import asyncio
import re
from pathlib import Path

import pytest
from abb_event_schema.dedup import content_hash
from abb_event_schema.event import Event
from abb_event_schema.ids import to_uuid
from abb_event_schema.parse import dumps, parse_event
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.db import tables as t
from abb_api.db.event_rows import row_to_event
from abb_api.ingestion.store import RUN_PROJECT_MISMATCH, IngestOutcome, PgEventStore
from tests.ingest_helpers import Tenant, build_event, make_run_ids, make_tenant


async def ingest(engine: AsyncEngine, tenant: Tenant, events: list[Event]) -> IngestOutcome:
    async with engine.begin() as conn:
        return await PgEventStore(conn, tenant.context).ingest(events)


async def scalar(engine: AsyncEngine, statement) -> int:  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        return int((await conn.execute(statement)).scalar_one())


def count_of(table) -> object:  # type: ignore[no-untyped-def]
    return select(func.count()).select_from(table)


async def pending_jobs(engine: AsyncEngine) -> int:
    return await scalar(
        engine,
        select(func.count()).select_from(t.outbox_jobs).where(t.outbox_jobs.c.status == "pending"),
    )


# ------------------------------------------------------------------ basics


async def test_a_batch_is_stored_with_its_run_agent_and_one_job(engine: AsyncEngine) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    events = [
        build_event(tenant, run, n=n, agent_version="sha256:abc" if n == 1 else None)
        for n in range(1, 6)
    ]
    outcome = await ingest(engine, tenant, events)
    assert (outcome.accepted, outcome.duplicates, outcome.conflicts, outcome.rejected) == (
        5,
        0,
        0,
        0,
    )
    assert [r.event_id for r in outcome.results] == [e.event_id for e in events]  # input order
    async with engine.connect() as conn:
        assert await scalar_in(conn, count_of(t.events)) == 5
        assert await scalar_in(conn, count_of(t.runs)) == 1
        assert await scalar_in(conn, count_of(t.agents)) == 1
        assert await scalar_in(conn, count_of(t.agent_versions)) == 1
        run_row = (await conn.execute(select(t.runs))).one()
        assert run_row.status == "RUNNING" and run_row.agent_slug == "coding-agent"
        assert run_row.started_at == events[0].occurred_at  # earliest event seeds the run
    assert await pending_jobs(engine) == 1


async def scalar_in(conn: AsyncConnection, statement) -> int:  # type: ignore[no-untyped-def]
    return int((await conn.execute(statement)).scalar_one())


async def test_empty_batch_is_a_no_op(engine: AsyncEngine) -> None:
    tenant = await make_tenant(engine, "acme")
    assert (await ingest(engine, tenant, [])).results == []


async def test_stored_events_round_trip_exactly(engine: AsyncEngine) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    attributes = {
        "llm.provider": "p",
        "llm.model": "m",
        "llm.input_tokens": 10,
        "vendor.x": [1, 2],
    }
    original = build_event(
        tenant,
        run,
        n=1,
        event_type="llm.request.completed",
        status="success",
        duration_ms=12.5,
        attributes=attributes,
        payload={"prompt": "hi", "nested": {"a": [1, None]}},
        tags=["a", "b"],
        sdk={"name": "abb", "version": "0.1"},
        parent_span_id=...,
    )
    await ingest(engine, tenant, [original])
    async with engine.connect() as conn:
        row = (await conn.execute(select(t.events))).one()
    restored = row_to_event(row)
    assert restored == original
    assert parse_event(dumps(restored)) == original
    assert bytes(row.content_hash).hex() == content_hash(original)


# ------------------------------------------------------------------ idempotency


async def test_retrying_the_same_batch_changes_nothing(engine: AsyncEngine) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    events = [build_event(tenant, run, n=n) for n in range(1, 4)]
    await ingest(engine, tenant, events)
    again = await ingest(engine, tenant, events)
    assert (again.accepted, again.duplicates, again.conflicts) == (0, 3, 0)
    assert await scalar(engine, count_of(t.events)) == 3
    assert await pending_jobs(engine) == 1  # a retry that adds nothing enqueues nothing new


async def test_partially_overlapping_retry_only_adds_the_new_events(engine: AsyncEngine) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    first = [build_event(tenant, run, n=n) for n in range(1, 4)]
    await ingest(engine, tenant, first)
    outcome = await ingest(engine, tenant, [*first[1:], build_event(tenant, run, n=4)])
    assert [r.status for r in outcome.results] == ["duplicate", "duplicate", "accepted"]


async def test_same_id_with_different_content_keeps_the_first_and_is_a_conflict(
    engine: AsyncEngine,
) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    original = build_event(tenant, run, n=1, status="success")
    await ingest(engine, tenant, [original])
    impostor = build_event(tenant, run, n=1, event_id=original.event_id, status="error")
    outcome = await ingest(engine, tenant, [impostor])
    assert (outcome.accepted, outcome.duplicates, outcome.conflicts) == (0, 0, 1)
    async with engine.connect() as conn:
        row = (await conn.execute(select(t.events))).one()
    assert row.status == "success"  # history was not rewritten (INV-1)
    assert bytes(row.content_hash).hex() == content_hash(original)


async def test_repeats_inside_one_batch(engine: AsyncEngine) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    a = build_event(tenant, run, n=1, status="success")
    clash = build_event(tenant, run, n=1, event_id=a.event_id, status="error")
    outcome = await ingest(engine, tenant, [a, a, clash, build_event(tenant, run, n=2)])
    assert [r.status for r in outcome.results] == ["accepted", "duplicate", "conflict", "accepted"]
    assert await scalar(engine, count_of(t.events)) == 2


# ------------------------------------------------------------------ out of order and jobs


async def test_arrival_order_does_not_matter_for_storage(engine: AsyncEngine) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    events = [build_event(tenant, run, n=n) for n in range(1, 8)]
    await ingest(engine, tenant, events[4:])  # the end of the run arrives first
    await ingest(engine, tenant, events[:2])
    await ingest(engine, tenant, events[2:4])
    async with engine.connect() as conn:
        rows = (await conn.execute(select(t.events.c.sequence).order_by(t.events.c.sequence))).all()
    assert [r.sequence for r in rows] == list(range(1, 8))
    assert await scalar(engine, count_of(t.runs)) == 1
    assert await pending_jobs(engine) == 1  # three batches coalesced into one job


async def test_a_job_that_is_already_running_does_not_block_a_new_one(engine: AsyncEngine) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    await ingest(engine, tenant, [build_event(tenant, run, n=1)])
    async with engine.begin() as conn:  # a worker has claimed it and is summarizing
        await conn.execute(update(t.outbox_jobs).values(status="running"))
    await ingest(engine, tenant, [build_event(tenant, run, n=2)])  # new event arrives mid-run
    assert await pending_jobs(engine) == 1
    assert await scalar(engine, count_of(t.outbox_jobs)) == 2


async def test_each_run_gets_its_own_job(engine: AsyncEngine) -> None:
    tenant = await make_tenant(engine, "acme")
    runs = [make_run_ids() for _ in range(3)]
    await ingest(engine, tenant, [build_event(tenant, r, n=1) for r in runs])
    assert await pending_jobs(engine) == 3


# ------------------------------------------------------------------ tenancy and projects


async def test_two_tenants_may_ingest_the_same_ids_without_interference(
    engine: AsyncEngine,
) -> None:
    a, b = await make_tenant(engine, "acme"), await make_tenant(engine, "globex")
    run = make_run_ids()
    event_id = build_event(a, run, n=1).event_id
    out_a = await ingest(engine, a, [build_event(a, run, n=1, event_id=event_id)])
    out_b = await ingest(engine, b, [build_event(b, run, n=1, event_id=event_id)])
    assert out_a.accepted == 1 and out_b.accepted == 1  # not a duplicate across tenants
    assert await scalar(engine, count_of(t.events)) == 2
    assert await scalar(engine, count_of(t.runs)) == 2


async def test_store_refuses_events_of_another_tenant(engine: AsyncEngine) -> None:
    a, b = await make_tenant(engine, "acme"), await make_tenant(engine, "globex")
    foreign = build_event(b, make_run_ids(), n=1)
    with pytest.raises(ValueError, match="tenant"):
        await ingest(engine, a, [foreign])
    assert await scalar(engine, count_of(t.events)) == 0


async def test_a_run_belongs_to_one_project(engine: AsyncEngine) -> None:
    tenant = await make_tenant(engine, "acme", projects=("alpha", "beta"))
    run = make_run_ids()
    await ingest(engine, tenant, [build_event(tenant, run, n=1, project="alpha")])
    outcome = await ingest(
        engine,
        tenant,
        [
            build_event(tenant, run, n=2, project="beta"),
            build_event(tenant, run, n=3, project="alpha"),
        ],
    )
    assert [(r.status, r.code) for r in outcome.results] == [
        ("rejected", RUN_PROJECT_MISMATCH),
        ("accepted", None),
    ]
    assert await scalar(engine, count_of(t.events)) == 2  # the foreign-project event was not stored


# ------------------------------------------------------------------ concurrency


async def test_fifty_concurrent_identical_batches_store_each_event_once(
    engine: AsyncEngine,
) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    events = [build_event(tenant, run, n=n) for n in range(1, 21)]

    outcomes = await asyncio.gather(*[ingest(engine, tenant, events) for _ in range(50)])
    assert sum(o.accepted for o in outcomes) == 20  # exactly one winner per event
    assert sum(o.duplicates for o in outcomes) == 20 * 49
    assert sum(o.conflicts for o in outcomes) == 0
    assert await scalar(engine, count_of(t.events)) == 20
    assert await pending_jobs(engine) == 1


async def test_overlapping_batches_in_opposite_order_do_not_deadlock(engine: AsyncEngine) -> None:
    tenant = await make_tenant(engine, "acme")
    runs = [make_run_ids() for _ in range(4)]
    events = [build_event(tenant, r, n=n) for r in runs for n in range(1, 11)]
    forward, backward = events, list(reversed(events))
    batches = [forward if i % 2 == 0 else backward for i in range(16)]

    outcomes = await asyncio.wait_for(
        asyncio.gather(*[ingest(engine, tenant, b) for b in batches]), timeout=30
    )
    assert sum(o.accepted for o in outcomes) == len(events)
    assert await scalar(engine, count_of(t.events)) == len(events)
    assert await pending_jobs(engine) == 4


async def test_concurrent_batches_with_conflicting_content_have_exactly_one_winner(
    engine: AsyncEngine,
) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    base = build_event(tenant, run, n=1, status="success")
    variants = [
        build_event(tenant, run, n=1, event_id=base.event_id, status=s)
        for s in ("success", "error", "timeout", "cancelled")
    ]
    outcomes = await asyncio.gather(*[ingest(engine, tenant, [v]) for v in variants * 5])
    assert sum(o.accepted for o in outcomes) == 1
    assert await scalar(engine, count_of(t.events)) == 1
    assert sum(o.duplicates + o.conflicts for o in outcomes) == len(outcomes) - 1


async def test_a_large_batch_is_chunked(engine: AsyncEngine) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    events = [build_event(tenant, run, n=n) for n in range(1, 1201)]
    outcome = await ingest(engine, tenant, events)
    assert outcome.accepted == 1200
    assert await scalar(engine, count_of(t.events)) == 1200


# ------------------------------------------------------------------ atomicity and immutability


async def test_a_failed_transaction_leaves_neither_events_nor_jobs(engine: AsyncEngine) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    events = [build_event(tenant, run, n=n) for n in range(1, 4)]
    with pytest.raises(RuntimeError, match="boom"):
        async with engine.begin() as conn:
            await PgEventStore(conn, tenant.context).ingest(events)
            raise RuntimeError("boom")  # e.g. the response could not be built
    assert await scalar(engine, count_of(t.events)) == 0
    assert await scalar(engine, count_of(t.outbox_jobs)) == 0
    assert await scalar(engine, count_of(t.runs)) == 0


def test_application_code_never_updates_or_deletes_events() -> None:
    """INV-1: the only statements against `events` are INSERT and SELECT."""
    source = Path(__file__).resolve().parents[1] / "src"
    forbidden = re.compile(r"(update|delete)\(\s*(t\.)?events\b|DELETE FROM events|UPDATE events")
    offenders = [p.name for p in source.rglob("*.py") if forbidden.search(p.read_text())]
    assert offenders == []


async def test_event_identity_uses_the_ulid_uuid_mapping(engine: AsyncEngine) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    event = build_event(tenant, run, n=1)
    await ingest(engine, tenant, [event])
    async with engine.connect() as conn:
        row = (await conn.execute(select(t.events.c.event_id, t.events.c.run_id))).one()
    assert row.event_id == to_uuid(event.event_id) and row.run_id == to_uuid(event.run_id)
