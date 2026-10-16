"""KI-057 (ADR-160): a change that commits while an analytics refresh runs is never lost.

Each interleaving is driven by explicit transaction control (a writer on its own connection that
commits at a chosen moment), never by timing. Where a step must wait for a lock, the test waits
for the database to say so (`pg_locks`), not for a duration.
"""

import asyncio
import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.analytics.dirty import begin_refresh, mark_dirty, refresh_with_marker, settle
from abb_api.jobs import handlers
from abb_api.jobs.outbox import REFRESH_ANALYTICS_DAY, Job
from abb_api.tenancy import TenantContext
from tests.analytics_fixtures import D6, Seeder
from tests.api_fixtures import Api
from tests.ingest_helpers import make_tenant
from tests.retention.enforce_helpers import scalar
from tests.retention.helpers import NOW, seed_run

DAY = NOW.date()


async def versions(engine: AsyncEngine, tenant: TenantContext, day: date = DAY) -> tuple[int, int]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT version, refreshed_version FROM analytics_dirty "
                    "WHERE workspace_id = :w AND day = :d"
                ),
                {"w": tenant.workspace_id, "d": day},
            )
        ).one()
    return row[0], row[1]


async def blocked_backends(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int(
            (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_locks WHERE NOT granted AND locktype <> 'advisory'"
                    )
                )
            ).scalar_one()
        )


async def wait_until_blocked(engine: AsyncEngine) -> None:
    for _ in range(400):
        if await blocked_backends(engine):
            return
        await asyncio.sleep(0.025)
    raise AssertionError("the refresh never waited on the writer's lock")


@pytest.fixture
async def tenant(engine: AsyncEngine) -> TenantContext:
    return (await make_tenant(engine, "acme")).context


async def test_the_writer_bumps_the_version_and_the_refresh_settles_it(
    engine: AsyncEngine, tenant: TenantContext
) -> None:
    async with engine.begin() as conn:
        await mark_dirty(conn, tenant, DAY)
        await mark_dirty(conn, tenant, DAY)
    assert await versions(engine, tenant) == (2, 0)
    async with engine.begin() as conn:
        seen = await begin_refresh(conn, tenant, DAY)
        assert seen == 2
        assert await settle(conn, tenant, DAY, seen) is True
    assert await versions(engine, tenant) == (2, 2)  # clean: nothing to redo


async def test_a_change_committed_after_the_rebuild_makes_the_refresh_ask_again(
    engine: AsyncEngine, tenant: TenantContext
) -> None:
    """The lost update of KI-057: the writer coalesced into a job that had already read the day."""
    async with engine.begin() as conn:
        await mark_dirty(conn, tenant, DAY)  # an earlier change: version 1
    writer = await engine.connect()
    tx = await writer.begin()
    await mark_dirty(writer, tenant, DAY)  # the writer's change, not yet committed: version 2

    async def writer_commits() -> None:
        await tx.commit()

    try:
        async with engine.begin() as conn:
            dirty = await refresh_with_marker(conn, tenant, DAY, after_refresh=writer_commits)
    finally:
        await writer.close()
    assert dirty is True  # the refresh read version 1; the writer committed 2 before the CAS
    assert await versions(engine, tenant) == (2, 0)  # and nothing was marked as absorbed


async def test_the_refresh_waits_for_an_open_writer_and_then_sees_its_version(
    engine: AsyncEngine, tenant: TenantContext
) -> None:
    async with engine.begin() as conn:
        await mark_dirty(conn, tenant, DAY)
    writer = await engine.connect()
    tx = await writer.begin()
    await mark_dirty(writer, tenant, DAY)  # holds the row lock until it commits

    async def refresh() -> bool:
        async with engine.begin() as conn:
            return await refresh_with_marker(conn, tenant, DAY)

    task = asyncio.create_task(refresh())
    try:
        await wait_until_blocked(engine)  # the compare-and-set is waiting on the writer
        assert not task.done()
        await tx.commit()
        assert await task is True  # it re-read the committed version: 2 != 1
    finally:
        await writer.close()
        if not task.done():
            task.cancel()
    assert await versions(engine, tenant) == (2, 0)


async def test_a_change_committed_before_the_refresh_began_is_absorbed_and_clean(
    engine: AsyncEngine,
) -> None:
    acme = await make_tenant(engine, "acme")
    tenant = acme.context
    async with engine.begin() as conn:
        await mark_dirty(conn, tenant, DAY)
    await seed_run(engine, acme, ended=NOW, events=1, spans=0, jobs=0)
    async with engine.begin() as conn:
        dirty = await refresh_with_marker(conn, tenant, DAY)
    assert dirty is False
    assert await versions(engine, tenant) == (1, 1)
    assert (
        await scalar(engine, "SELECT sum(runs) FROM analytics_runs_daily WHERE day = :d", d=DAY)
        == 1
    )


async def test_a_first_ever_refresh_waits_for_a_writer_that_created_the_marker(
    engine: AsyncEngine, tenant: TenantContext
) -> None:
    """No marker row exists yet and a writer has inserted one without committing: the refresh does
    not read past it (it would absorb version 0 and miss the change)."""
    writer = await engine.connect()
    tx = await writer.begin()
    await mark_dirty(writer, tenant, DAY)

    async def refresh() -> bool:
        async with engine.begin() as conn:
            return await refresh_with_marker(conn, tenant, DAY)

    task = asyncio.create_task(refresh())
    try:
        await wait_until_blocked(engine)
        await tx.commit()
        assert await task is False  # it began after the commit: version 1 seen and absorbed
    finally:
        await writer.close()
        if not task.done():
            task.cancel()
    assert await versions(engine, tenant) == (1, 1)


async def test_the_job_queues_another_refresh_exactly_when_the_day_was_dirtied_meanwhile(
    engine: AsyncEngine, tenant: TenantContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = Job(
        id=uuid.uuid4(), job_type=REFRESH_ANALYTICS_DAY, workspace_id=tenant.workspace_id,
        dedupe_key=f"{tenant.workspace_id}:{DAY}", payload={"day": DAY.isoformat()},
        attempt_count=1, lease_owner="t",
    )  # fmt: skip

    async def pending() -> int:
        return int(
            await scalar(
                engine,
                "SELECT count(*) FROM outbox_jobs WHERE job_type = 'refresh_analytics_day' "
                "AND status = 'pending'",
            )
        )

    async with engine.begin() as conn:  # no concurrent change: the handler adds nothing
        await handlers.refresh_analytics_day(job, conn)
    assert await pending() == 0

    async def bumped(conn: Any, t: TenantContext, day: date, **_: Any) -> bool:
        async def other_writer() -> None:
            async with engine.begin() as w:
                await mark_dirty(w, t, day)

        return await refresh_with_marker(conn, t, day, after_refresh=other_writer)

    monkeypatch.setattr("abb_api.jobs.handlers.refresh_with_marker", bumped)
    async with engine.begin() as conn:
        await handlers.refresh_analytics_day(job, conn)
    assert await pending() == 1  # a change slipped in: one more refresh is queued, coalesced
    async with engine.begin() as conn:
        await handlers.refresh_analytics_day(job, conn)
    assert await pending() == 1  # and it does not pile up


async def test_summarizing_a_run_marks_its_day_and_the_refresh_settles_it(api: Api) -> None:
    await Seeder(api, "writer").run(D6, llm=[], tools=[("t", "success", 5)])
    await api.drain()
    async with api.engine.connect() as conn:
        rows = (
            await conn.execute(text("SELECT version, refreshed_version FROM analytics_dirty"))
        ).all()
    assert rows and all(r.version >= 1 and r.refreshed_version == r.version for r in rows)
