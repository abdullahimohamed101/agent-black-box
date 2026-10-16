"""Worker job metrics: each outcome, the duration, and the real handlers on a real ingest."""

from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.db import tables as t
from abb_api.jobs.outbox import Job
from abb_api.jobs.worker import Worker
from abb_api.obs.catalogue import Observability, build_observability
from tests.api_fixtures import Api, Tick
from tests.conftest import make_settings
from tests.ingest_helpers import make_run_ids, wire_event
from tests.obs.helpers import observations, total, value
from tests.test_outbox_worker import T0, add_job, workspace


def worker_for(
    engine: AsyncEngine, database_url: str, obs: Observability, handlers: dict[str, Any], **kw: Any
) -> Worker:
    return Worker(
        engine,
        handlers,
        make_settings(database_url, **kw),
        clock=Tick(T0),
        owner="w1",
        metrics=obs.catalogue,
    )


async def test_success_retry_and_dead_letter_are_counted_by_type_and_outcome(
    engine: AsyncEngine, database_url: str
) -> None:
    obs = build_observability()
    ws = await workspace(engine)
    attempts = {"n": 0}

    async def fine(job: Job, conn: AsyncConnection) -> None:
        return None

    async def flaky(job: Job, conn: AsyncConnection) -> None:
        attempts["n"] += 1
        raise RuntimeError("boom")

    async with engine.begin() as conn:
        await add_job(conn, ws, job_type="fine")
        await add_job(conn, ws, job_type="flaky")
    clock = Tick(T0)
    worker = Worker(
        engine,
        {"fine": fine, "flaky": flaky},
        make_settings(database_url, worker_max_attempts=2),
        clock=clock,
        owner="w1",
        metrics=obs.catalogue,
    )
    assert await worker.run_once() == 2
    assert value(obs, "worker_jobs_total", type="fine", outcome="succeeded") == 1
    assert value(obs, "worker_jobs_total", type="flaky", outcome="retried") == 1
    assert value(obs, "processor_failures_total", type="flaky") == 1
    assert value(obs, "processor_failures_total", type="fine") == 0
    clock.now += timedelta(hours=1)  # past the backoff: the second and last attempt
    assert await worker.run_once() == 1
    assert value(obs, "worker_jobs_total", type="flaky", outcome="dead_letter") == 1
    assert value(obs, "processor_failures_total", type="flaky") == 2
    assert attempts["n"] == 2
    assert observations(obs, "worker_job_duration_seconds", type="fine") == 1
    assert observations(obs, "worker_job_duration_seconds", type="flaky") == 2
    assert total(obs, "worker_jobs_total") == 3


async def test_a_lost_lease_and_a_missing_handler_and_a_superseded_job(
    engine: AsyncEngine, database_url: str
) -> None:
    obs = build_observability()
    ws = await workspace(engine)

    async def thief(job: Job, conn: AsyncConnection) -> None:
        async with engine.begin() as other:
            await other.execute(
                update(t.outbox_jobs).where(t.outbox_jobs.c.id == job.id).values(lease_owner="w2")
            )

    async def newer(job: Job, conn: AsyncConnection) -> None:
        async with engine.begin() as other:  # newer work for the same key is already queued
            await add_job(other, ws, job_type="newer", key="same")
        raise RuntimeError("boom")

    async with engine.begin() as conn:
        await add_job(conn, ws, job_type="thief")
        await add_job(conn, ws, job_type="mystery")
        await add_job(conn, ws, job_type="newer", key="same")
    worker = worker_for(engine, database_url, obs, {"thief": thief, "newer": newer})
    await worker.run_once()
    assert value(obs, "worker_jobs_total", type="thief", outcome="lease_lost") == 1
    assert value(obs, "worker_jobs_total", type="mystery", outcome="dead_letter") == 1
    assert value(obs, "processor_failures_total", type="mystery") == 1
    assert value(obs, "worker_jobs_total", type="newer", outcome="superseded") == 1
    # A job with no handler has no duration; the others each have one.
    assert observations(obs, "worker_job_duration_seconds", type="mystery") == 0
    assert observations(obs, "worker_job_duration_seconds", type="thief") == 1


async def test_the_real_handlers_count_as_many_successes_as_jobs_that_finished(
    observed: tuple[Api, Observability],
) -> None:
    api, obs = observed
    run = make_run_ids()
    assert (await api.post_batch([wire_event(run, 1), wire_event(run, 2)])).status_code == 202
    await api.drain()
    async with api.engine.connect() as conn:
        done = (
            await conn.execute(select(func.count()).where(t.outbox_jobs.c.status == "done"))
        ).scalar_one()
        types = {
            r[0]
            for r in await conn.execute(
                select(t.outbox_jobs.c.job_type).where(t.outbox_jobs.c.status == "done")
            )
        }
    assert done >= 1
    ok = sum(
        value(obs, "worker_jobs_total", type=job_type, outcome="succeeded") for job_type in types
    )
    assert ok == done, "every finished job was counted once, after its commit"
    assert "summarize_run" in types
    assert total(obs, "processor_failures_total") == 0


async def test_an_unknown_job_type_cannot_grow_the_series_without_bound() -> None:
    obs = build_observability()
    for n in range(500):
        obs.catalogue.worker_jobs_total.inc(f"type-{n}", "succeeded")
    series = obs.registry.metrics["worker_jobs_total"].series_count()
    assert series <= 33, series  # 32 types plus the overflow bucket
