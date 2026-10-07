"""End to end: events -> outbox -> worker -> derived run and spans, and its rebuildability."""

import asyncio
import random
import uuid
from typing import Any

import pytest
from abb_event_schema.event import Event
from abb_event_schema.ids import IdKind, new_id, to_uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.db import tables as t
from abb_api.ingestion.store import PgEventStore
from abb_api.jobs.handlers import HANDLERS
from abb_api.jobs.worker import Worker
from abb_api.runs.repository import RunRepository
from abb_api.runs.summary import SUMMARY_VERSION, derive_run
from tests.conftest import make_settings
from tests.ingest_helpers import Tenant, build_event, make_run_ids, make_tenant


def worker_for(engine: AsyncEngine, database_url: str) -> Worker:
    # real clock: jobs get their available_at from the database's now()
    return Worker(engine, HANDLERS, make_settings(database_url), owner="test-worker")


async def drain(worker: Worker) -> None:
    for _ in range(50):
        if await worker.run_once() == 0:
            return
    raise AssertionError("worker never ran out of jobs")


async def ingest(engine: AsyncEngine, tenant: Tenant, events: list[Event]) -> None:
    async with engine.begin() as conn:
        await PgEventStore(conn, tenant.context).ingest(events)


async def run_row(engine: AsyncEngine, run_id: str) -> Any:
    async with engine.connect() as conn:
        return (await conn.execute(select(t.runs).where(t.runs.c.id == to_uuid(run_id)))).one()


async def span_rows(engine: AsyncEngine, run_id: str) -> dict[uuid.UUID, Any]:
    async with engine.connect() as conn:
        rows = await conn.execute(select(t.spans).where(t.spans.c.run_id == to_uuid(run_id)))
        return {r.id: r for r in rows}


def tool_span(
    tenant: Tenant,
    run: dict[str, str],
    n: int,
    span: str,
    parent: str | None,
    name: str,
    project: str = "p",
) -> list[Event]:
    """A started/completed pair for one tool span (two sequence numbers starting at n)."""
    attrs = {"tool.name": name}
    common: dict[str, Any] = {
        "span_id": span,
        "parent_span_id": parent or ...,
        "project": project,
    }
    return [
        build_event(tenant, run, n=n, event_type="tool.call.started", attributes=attrs, **common),
        build_event(
            tenant, run, n=n + 1, event_type="tool.call.completed", attributes=attrs,
            status="success", duration_ms=1000, **common,
        ),
    ]  # fmt: skip


def scenario(tenant: Tenant, run: dict[str, str], project: str = "p") -> list[Event]:
    """A run with nested spans, an LLM call, a failure, a retry, files, and a late event."""
    root = new_id(IdKind.SPAN)
    child_a, child_b, grandchild = (new_id(IdKind.SPAN) for _ in range(3))
    llm = {"llm.provider": "p", "llm.model": "m", "llm.input_tokens": 7, "cost.estimated_usd": 0.02}

    def ev(n: int, kind: str, attributes: dict[str, Any] | None = None, **kw: Any) -> Event:
        kw.setdefault("project", project)
        return build_event(tenant, run, n=n, event_type=kind, attributes=attributes or {}, **kw)

    return [
        ev(1, "run.started", {"run.name": "Fix bug"}, span_id=...),
        ev(2, "agent.started", span_id=root),
        *tool_span(tenant, run, 3, child_a, root, "github", project),
        ev(5, "llm.request.completed", llm, span_id=child_b, parent_span_id=root, status="success"),
        *tool_span(tenant, run, 6, grandchild, child_b, "shell", project),
        ev(
            8,
            "tool.call.failed",
            {"tool.name": "x"},
            span_id=new_id(IdKind.SPAN),
            parent_span_id=root,
            status="error",
        ),
        ev(9, "retry.attempted", {"retry.attempt": 1}, span_id=...),
        ev(10, "file.modified", {"file.path": "a.py"}, span_id=...),
        ev(11, "agent.completed", span_id=root, status="success"),
        ev(12, "run.completed", span_id=...),
        ev(13, "tool.call.completed", {"tool.name": "late"}, status="success"),  # arrives late
    ]


# ------------------------------------------------------------------ the basic flow


async def test_ingested_events_become_a_derived_run_and_spans(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    events = scenario(tenant, run)
    await ingest(engine, tenant, events)
    before = await run_row(engine, run["run_id"])
    assert before.summary == {} and before.summary_version == 0  # not derived yet: "processing"

    await drain(worker_for(engine, database_url))
    row = await run_row(engine, run["run_id"])
    assert (
        row.status == "SUCCESS" and row.name == "Fix bug" and row.summary_version == SUMMARY_VERSION
    )
    assert row.started_at == events[0].occurred_at and row.completed_at == events[11].occurred_at
    assert row.ordering_mode == "sequence"
    summary = row.summary
    assert summary["event_count"] == 13 and summary["llm_calls"] == 1 and summary["tool_calls"] == 4
    assert (
        summary["retry_count"] == 1
        and summary["error_count"] == 1
        and summary["files_modified"] == 1
    )
    assert summary["estimated_cost_usd"] == pytest.approx(0.02) and summary["models"] == ["m"]

    spans = await span_rows(engine, run["run_id"])
    by_name = {s.name: s for s in spans.values() if s.name}
    assert by_name["github"].parent_span_id == to_uuid(events[1].span_id or "")
    assert by_name["shell"].parent_span_id == to_uuid(events[4].span_id or "")
    assert by_name["github"].status == "success" and by_name["github"].duration_ms == 1000
    assert by_name["github"].started_at < by_name["github"].ended_at
    async with engine.connect() as conn:
        statuses = {r.status for r in await conn.execute(select(t.outbox_jobs.c.status))}
    assert statuses == {"done"}


async def test_late_events_update_the_run_without_reopening_it(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    events = scenario(tenant, run)
    worker = worker_for(engine, database_url)
    await ingest(engine, tenant, events[:-1])
    await drain(worker)
    assert (await run_row(engine, run["run_id"])).summary["event_count"] == 12
    await ingest(engine, tenant, events[-1:])  # arrives after run.completed was summarized
    await drain(worker)
    row = await run_row(engine, run["run_id"])
    assert (
        row.status == "SUCCESS"
        and row.summary["event_count"] == 13
        and row.summary["tool_calls"] == 4
    )


async def test_an_explicit_run_name_survives_unless_the_events_name_the_run(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant = await make_tenant(engine, "acme")
    worker = worker_for(engine, database_url)
    unnamed = make_run_ids()
    await ingest(
        engine,
        tenant,
        [
            build_event(
                tenant,
                unnamed,
                n=1,
                event_type="tool.call.completed",
                attributes={"tool.name": "t"},
            )
        ],
    )
    async with engine.begin() as conn:
        from sqlalchemy import update

        await conn.execute(update(t.runs).values(name="explicit"))
    await drain(worker)
    assert (await run_row(engine, unnamed["run_id"])).name == "explicit"
    named = make_run_ids()
    await ingest(
        engine,
        tenant,
        [
            build_event(
                tenant, named, n=1, event_type="run.started", attributes={"run.name": "from events"}
            )
        ],
    )
    await drain(worker)
    assert (await run_row(engine, named["run_id"])).name == "from events"


async def test_a_job_for_a_run_with_no_events_or_no_run_is_a_harmless_no_op(
    engine: AsyncEngine, database_url: str
) -> None:
    from abb_api.jobs.outbox import SUMMARIZE_RUN, OutboxRepository

    tenant = await make_tenant(engine, "acme")
    async with engine.begin() as conn:
        outbox = OutboxRepository(conn, tenant.context)
        await outbox.enqueue(
            job_type=SUMMARIZE_RUN, dedupe_key="gone", payload={"run_id": str(uuid.uuid4())}
        )
    await drain(worker_for(engine, database_url))
    async with engine.connect() as conn:
        assert {r.status for r in await conn.execute(select(t.outbox_jobs.c.status))} == {"done"}


# ------------------------------------------------------------------ rebuildability


def comparable(row: Any, spans: dict[uuid.UUID, Any]) -> dict[str, Any]:
    return {
        "status": row.status,
        "ordering_mode": row.ordering_mode,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "duration_ms": row.duration_ms,
        "name": row.name,
        "agent": row.agent_slug,
        "trace": row.trace_id,
        "summary": row.summary,
        "spans": {
            sid: (
                s.parent_span_id, s.name, s.kind, s.status,
                s.started_at, s.ended_at, s.duration_ms, s.event_count,
            )
            for sid, s in spans.items()
        },
    }  # fmt: skip


def expected_from(events: list[Event]) -> dict[str, Any]:
    d = derive_run(events)
    return {
        "status": d.status.value,
        "ordering_mode": d.ordering_mode,
        "started_at": d.started_at,
        "completed_at": d.completed_at,
        "duration_ms": d.duration_ms,
        "name": d.name,
        "agent": d.agent_slug,
        "trace": to_uuid(d.trace_id),
        "summary": d.summary,
        "spans": {
            to_uuid(s.span_id): (
                to_uuid(s.parent_span_id) if s.parent_span_id else None, s.name,
                s.kind.value if s.kind else None, s.status.value if s.status else None,
                s.started_at, s.ended_at, s.duration_ms, s.event_count,
            )
            for s in d.spans.values()
        },
    }  # fmt: skip


@pytest.mark.parametrize("seed", range(12))
async def test_state_equals_a_from_scratch_rebuild_for_any_batching_order_and_worker_timing(
    engine: AsyncEngine, database_url: str, seed: int
) -> None:
    rng = random.Random(seed)
    tenant, run = await make_tenant(engine, f"t{seed}"), make_run_ids()
    events = scenario(tenant, run)
    worker = worker_for(engine, database_url)

    # random arrival: shuffled, split into random batches, with duplicates, workers interleaved
    arrival = events[:]
    rng.shuffle(arrival)
    arrival += rng.sample(events, k=rng.randint(0, 6))  # retries
    cuts = sorted(rng.sample(range(1, len(arrival)), k=rng.randint(1, 5)))
    batches = [arrival[a:b] for a, b in zip([0, *cuts], [*cuts, len(arrival)], strict=True)]
    for batch in batches:
        await ingest(engine, tenant, batch)
        if rng.random() < 0.6:
            await drain(worker)  # sometimes summarize mid-stream, sometimes not at all
    await drain(worker)

    row, spans = await run_row(engine, run["run_id"]), await span_rows(engine, run["run_id"])
    assert comparable(row, spans) == expected_from(events)

    # rebuilding again (a replayed job) changes nothing
    async with engine.begin() as conn:
        from abb_api.jobs.outbox import SUMMARIZE_RUN, OutboxRepository

        await OutboxRepository(conn, tenant.context).enqueue(
            job_type=SUMMARIZE_RUN,
            dedupe_key="rebuild",
            payload={"run_id": str(to_uuid(run["run_id"]))},
        )
    await drain(worker)
    assert comparable(
        await run_row(engine, run["run_id"]), await span_rows(engine, run["run_id"])
    ) == expected_from(events)


async def test_runs_of_different_tenants_are_derived_independently(
    engine: AsyncEngine, database_url: str
) -> None:
    a, b = await make_tenant(engine, "acme"), await make_tenant(engine, "globex")
    run = make_run_ids()  # the same run id in both workspaces
    await ingest(engine, a, scenario(a, run))
    await ingest(engine, b, [build_event(b, run, n=1, event_type="run.started", attributes={})])
    await drain(worker_for(engine, database_url))
    async with engine.connect() as conn:
        rows = {r.workspace_id: r for r in await conn.execute(select(t.runs))}
    assert rows[a.context.workspace_id].summary["event_count"] == 13
    assert rows[b.context.workspace_id].summary["event_count"] == 1
    assert rows[b.context.workspace_id].status == "RUNNING"


# ------------------------------------------------------------------ concurrency and ownership


async def test_a_slow_job_cannot_overwrite_newer_state_with_a_stale_snapshot(
    engine: AsyncEngine, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Job 1 reads, new events arrive and job 2 completes, then job 1 writes: stale state wins.

    The per-run lock makes job 2 wait for job 1 and read the events only afterwards.
    """
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    events = scenario(tenant, run)
    await ingest(engine, tenant, events[:6])

    started, gate = asyncio.Event(), asyncio.Event()
    original = PgEventStore.load_run
    calls = {"n": 0}

    async def slow_first_read(self: PgEventStore, run_id: uuid.UUID) -> list[Event]:
        result = await original(self, run_id)
        calls["n"] += 1
        if calls["n"] == 1:
            started.set()
            await gate.wait()  # the first job is holding an older snapshot
        return result

    monkeypatch.setattr(PgEventStore, "load_run", slow_first_read)
    first = asyncio.create_task(worker_for(engine, database_url).run_once())
    await asyncio.wait_for(started.wait(), 5)
    await ingest(engine, tenant, events[6:])  # newer events commit and queue job 2
    second = asyncio.create_task(worker_for(engine, database_url).run_once())
    await asyncio.sleep(0.3)  # job 2 is now waiting on the run lock
    assert not second.done()
    gate.set()
    await asyncio.wait_for(asyncio.gather(first, second), 10)
    await drain(worker_for(engine, database_url))

    row = await run_row(engine, run["run_id"])
    assert row.summary["event_count"] == len(events) and row.status == "SUCCESS"


async def test_a_span_id_that_belongs_to_another_run_is_never_taken_over(
    engine: AsyncEngine,
) -> None:
    tenant = await make_tenant(engine, "acme")
    shared = new_id(IdKind.SPAN)
    runs = [make_run_ids(), make_run_ids()]
    events = [
        build_event(
            tenant, r, n=1, event_type="tool.call.started", span_id=shared,
            attributes={"tool.name": name},
        )
        for r, name in zip(runs, ("first", "second"), strict=True)
    ]  # fmt: skip
    for e in events:
        await ingest(engine, tenant, [e])
    async with engine.begin() as conn:
        repo = RunRepository(conn, tenant.context)
        assert await repo.apply_derivation(to_uuid(runs[0]["run_id"]), derive_run([events[0]])) == 0
        skipped = await repo.apply_derivation(to_uuid(runs[1]["run_id"]), derive_run([events[1]]))
    assert skipped == 1
    spans = await span_rows(engine, runs[0]["run_id"])
    assert [s.name for s in spans.values()] == ["first"]
    assert await span_rows(engine, runs[1]["run_id"]) == {}


async def test_a_run_with_thousands_of_spans_is_summarized(
    engine: AsyncEngine, database_url: str
) -> None:
    """Regression: one statement per run exceeded asyncpg's 32767 bind-parameter limit at about
    2500 spans, so big runs failed, retried and were dead-lettered (found by the benchmark)."""
    tenant, run = await make_tenant(engine, "acme"), make_run_ids()
    events = [
        build_event(
            tenant, run, n=n, event_type="tool.call.completed", attributes={"tool.name": "t"}
        )
        for n in range(1, 3001)
    ]
    for start in range(0, len(events), 1000):
        await ingest(engine, tenant, events[start : start + 1000])
    await drain(worker_for(engine, database_url))
    row = await run_row(engine, run["run_id"])
    assert row.summary["event_count"] == 3000 and row.summary_version == SUMMARY_VERSION
    assert len(await span_rows(engine, run["run_id"])) == 3000
    async with engine.connect() as conn:
        statuses = {r.status for r in await conn.execute(select(t.outbox_jobs.c.status))}
    assert statuses == {"done"}
