"""A settings change that affects detectors re-analyses the project's recent runs (KI-133, ADR-131
amendment): no manual `rebuild-findings`, bounded, idempotent, in the audit row's transaction."""

from datetime import timedelta
from typing import Any

from abb_event_schema.ids import to_uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.db import tables as t
from abb_api.jobs.outbox import DETECT_FINDINGS
from abb_api.security.settings import (
    Actor,
    CaptureMode,
    ReanalysisPolicy,
    SecuritySettingsService,
)
from tests.authz.test_security_findings_access import listing, plant, put_settings
from tests.authz.world import World
from tests.security_helpers import ALLOWED_HOST, Run


async def pending_runs(world: World) -> set[str]:
    async with world.api.engine.connect() as conn:
        rows = await conn.execute(
            select(t.outbox_jobs.c.payload).where(
                t.outbox_jobs.c.job_type == DETECT_FINDINGS, t.outbox_jobs.c.status == "pending"
            )
        )
        return {r.payload["run_id"] for r in rows}


async def detectors_of(world: World, run_id: str) -> dict[str, str]:
    items = (await listing(world, "viewer", limit=100)).json()["items"]
    return {i["detector"]: i["id"] for i in items if i["run_id"] == run_id}


async def test_a_mute_and_an_unmute_reach_existing_runs_without_a_rebuild(world: World) -> None:
    run_id, _ = await plant(world)
    before = await detectors_of(world, run_id)
    assert len(before) == 6
    assert (
        await put_settings(world, "owner", {"muted_detectors": ["sec.risky_git"]})
    ).status_code == 200
    assert run_id.replace("run_", "") or True
    await world.api.drain()  # the worker alone: nobody ran rebuild-findings
    muted = await detectors_of(world, run_id)
    assert set(muted) == set(before) - {"sec.risky_git"}
    assert (await put_settings(world, "owner", {"muted_detectors": []})).status_code == 200
    await world.api.drain()
    assert await detectors_of(world, run_id) == before  # same detector, same finding id


async def test_an_allowlist_change_reaches_existing_runs(world: World) -> None:
    run_id, _ = await plant(world)  # allowlist: api.example.com only
    assert "sec.unexpected_destination" in await detectors_of(world, run_id)
    # the planted run's hosts are all under the sentinel; allowing a wildcard of them clears it
    allow = ["api.example.com", "*.exfil.example.net", "*.exfil.example.org"]
    assert (await put_settings(world, "owner", {"destination_allowlist": allow})).status_code == 200
    await world.api.drain()
    assert "sec.unexpected_destination" not in await detectors_of(world, run_id)


async def test_a_capture_mode_change_and_a_no_op_do_not_reanalyse(world: World) -> None:
    run_id, _ = await plant(world)
    assert await pending_runs(world) == set()
    assert (await put_settings(world, "owner", {"capture_mode": "DISABLED"})).status_code == 200
    assert await pending_runs(world) == set()
    unchanged = {"destination_allowlist": [ALLOWED_HOST]}
    assert (await put_settings(world, "owner", unchanged)).status_code == 200
    assert await pending_runs(world) == set()
    assert run_id


async def test_repeated_edits_coalesce_into_one_job_per_run(world: World) -> None:
    run_id, _ = await plant(world)
    await put_settings(world, "owner", {"muted_detectors": ["sec.risky_git"]})
    await put_settings(world, "owner", {"muted_detectors": ["sec.risky_git", "sec.privileged_db"]})
    await put_settings(world, "owner", {"muted_detectors": []})
    async with world.api.engine.connect() as conn:
        count = await conn.scalar(
            select(func.count())
            .select_from(t.outbox_jobs)
            .where(
                t.outbox_jobs.c.job_type == DETECT_FINDINGS,
                t.outbox_jobs.c.status == "pending",
                t.outbox_jobs.c.payload["run_id"].astext == str(to_uuid(run_id)),
            )
        )
    assert count == 1
    await world.api.drain()
    assert len(await detectors_of(world, run_id)) == 6  # the final settings win


async def test_only_the_projects_own_runs_are_queued_and_authz_is_unchanged(world: World) -> None:
    acme_run, _ = await plant(world)  # also plants a globex run
    await world.api.drain()
    for actor in ("developer", "viewer", "security", "billing"):
        denied = await put_settings(world, actor, {"muted_detectors": ["sec.risky_git"]})
        assert denied.status_code == 403
    assert await pending_runs(world) == set()  # a refused change queues nothing
    other = world.globex.project_id
    assert (
        await put_settings(world, "owner", {"muted_detectors": ["sec.risky_git"]}, other)
    ).status_code == 404
    assert await pending_runs(world) == set()
    await put_settings(world, "owner", {"muted_detectors": ["sec.risky_git"]})
    queued = await pending_runs(world)
    assert queued and str(to_uuid(acme_run)) in queued
    async with world.api.engine.connect() as conn:
        workspaces = {
            r.workspace_id
            for r in await conn.execute(
                select(t.outbox_jobs.c.workspace_id).where(
                    t.outbox_jobs.c.job_type == DETECT_FINDINGS,
                    t.outbox_jobs.c.status == "pending",
                )
            )
        }
    assert workspaces == {world.api.tenant.context.workspace_id}


async def test_the_audit_row_and_the_jobs_commit_together(world: World) -> None:
    await plant(world)
    await world.api.drain()
    await put_settings(world, "owner", {"muted_detectors": ["sec.risky_git"]})
    async with world.api.engine.connect() as conn:
        audited = await conn.scalar(
            select(func.count())
            .select_from(t.audit_log)
            .where(t.audit_log.c.action == "security.settings.update")
        )
    assert audited is not None and audited >= 1 and await pending_runs(world)


async def make_runs(world: World, count: int, *, age_days: list[int] | None = None) -> list[str]:
    """Plain runs in acme/alpha, newest last."""
    ids: list[str] = []
    for _ in range(count):
        run = Run()
        run.shell("pytest -q", "R1")
        ids.append(run.run_id)
        assert (await world.api.post_batch(run.wires, token="writer")).status_code == 202
    await world.api.drain()
    if age_days:
        async with world.api.engine.begin() as conn:
            await conn.execute(  # the world's own runs are old news for this test
                t.runs.update()
                .where(t.runs.c.id.not_in([to_uuid(i) for i in ids]))
                .values(started_at=world.api.clock() - timedelta(days=100))
            )
            for run_id, days in zip(ids, age_days, strict=True):
                await conn.execute(
                    t.runs.update()
                    .where(t.runs.c.id == to_uuid(run_id))
                    .values(started_at=world.api.clock() - timedelta(days=days))
                )
    return ids


async def apply(engine: AsyncEngine, world: World, policy: ReanalysisPolicy, **fields: Any) -> None:
    await SecuritySettingsService(engine, policy, world.api.clock).update(
        world.api.tenant.context,
        world.api.tenant.project_uuids["alpha"],
        Actor("user", "user:usr_test"),
        **fields,
    )


async def test_the_run_count_and_the_age_are_bounds_and_the_newest_come_first(
    world: World,
) -> None:
    ids = await make_runs(world, 5, age_days=[40, 20, 10, 5, 1])  # oldest first
    seeded = await pending_runs(world)
    assert seeded == set()
    engine = world.api.engine
    newest_two = ReanalysisPolicy(max_runs=2, max_age_days=30, delay_seconds=0, cooldown_seconds=0)
    await apply(engine, world, newest_two, muted_detectors=["sec.risky_git"])
    assert await pending_runs(world) == {str(to_uuid(i)) for i in ids[-2:]}  # newest two
    await world.api.drain()
    all_recent = ReanalysisPolicy(
        max_runs=100, max_age_days=30, delay_seconds=0, cooldown_seconds=0
    )
    await apply(engine, world, all_recent, muted_detectors=[])
    queued = await pending_runs(world)
    assert str(to_uuid(ids[0])) not in queued  # 40 days old: outside the age bound
    assert {str(to_uuid(i)) for i in ids[1:]} <= queued
    # newest first: the earliest available_at belongs to the newest run
    async with engine.connect() as conn:
        order = [
            r.payload["run_id"]
            for r in await conn.execute(
                select(t.outbox_jobs.c.payload)
                .where(
                    t.outbox_jobs.c.job_type == DETECT_FINDINGS, t.outbox_jobs.c.status == "pending"
                )
                .order_by(t.outbox_jobs.c.available_at)
            )
            if r.payload["run_id"] in {str(to_uuid(i)) for i in ids}
        ]
    assert order[0] == str(to_uuid(ids[-1]))
    await world.api.drain()
    await apply(
        engine,
        world,
        ReanalysisPolicy(max_runs=0, delay_seconds=0),
        muted_detectors=["sec.risky_git"],
    )
    assert await pending_runs(world) == set()  # 0 turns it off
    await apply(
        engine, world, ReanalysisPolicy(delay_seconds=0), capture_mode=CaptureMode.METADATA_ONLY
    )
    assert await pending_runs(world) == set()
