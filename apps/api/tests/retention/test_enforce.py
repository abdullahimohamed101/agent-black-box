"""Enforcement (ADR-160): the destructive path against real Postgres and a real artifact directory.

The tick runs as the real retention role. Times are a fixed `NOW`; ordering is driven by hooks,
never by sleeps; nothing here asserts a wall-clock threshold.
"""

import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.artifacts.store import LocalFsArtifactStore
from abb_api.db import tables as t
from abb_api.retention.tick import run_tick
from tests.ingest_helpers import Tenant, make_tenant
from tests.retention.enforce_helpers import (
    RUN_KEYED,
    enforce,
    enforcing,
    everything,
    fingerprint,
    make_store,
    scalar,
    tombstones,
)
from tests.retention.helpers import NOW, SeededRun, days_ago, seed_run
from tests.retention.test_tick import settings


@pytest.fixture
def store(tmp_path: Path) -> LocalFsArtifactStore:
    return make_store(tmp_path)


async def files_exist(store: LocalFsArtifactStore, run: SeededRun) -> list[bool]:
    out = []
    for key in run.artifact_keys:
        try:
            await store.read(key, 0, 1)
            out.append(True)
        except Exception:
            out.append(False)
    return out


async def seed_four(engine: AsyncEngine, tenant: Tenant, store: LocalFsArtifactStore):  # type: ignore[no-untyped-def]
    """One run per stage of life under the 30/90/365 day defaults."""
    kwargs = {"store": store, "full": True, "artifact_sizes": (100, 200), "events": 3, "spans": 2}
    gone = await seed_run(engine, tenant, ended=days_ago(400), **kwargs)  # type: ignore[arg-type]
    expired_trace = await seed_run(engine, tenant, ended=days_ago(100), **kwargs)  # type: ignore[arg-type]
    expired_files = await seed_run(engine, tenant, ended=days_ago(40), **kwargs)  # type: ignore[arg-type]
    fresh = await seed_run(engine, tenant, ended=days_ago(10), **kwargs)  # type: ignore[arg-type]
    return gone, expired_trace, expired_files, fresh


# ---------------------------------------------------------------- dry run deletes nothing


async def test_dry_run_deletes_nothing_in_any_table(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    runs = await seed_four(engine, acme, store)
    before = await everything(engine, skip=("retention_reports",))
    result = await run_tick(
        retention_engine, settings(), budget_seconds=60, keep_reports=50, now=lambda: NOW
    )
    assert result.mode == "dry_run" and result.deleted_runs == 0
    assert await everything(engine, skip=("retention_reports",)) == before
    for run in runs:
        assert all(await files_exist(store, run))
    assert await tombstones(engine) == []


async def test_off_does_nothing_and_enforce_without_a_store_is_refused(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    await seed_four(engine, acme, store)
    before = await everything(engine)
    off = await run_tick(
        retention_engine,
        settings(retention_mode="off"),
        budget_seconds=60,
        keep_reports=50,
        now=lambda: NOW,
    )
    refused = await run_tick(
        retention_engine, enforcing(), budget_seconds=60, keep_reports=50, now=lambda: NOW
    )
    assert off.reports == 0 and refused.refused
    assert await everything(engine) == before


# ---------------------------------------------------------------- what enforce deletes


async def test_enforce_deletes_exactly_the_expired_tiers_of_each_run(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    gone, expired_trace, expired_files, fresh = await seed_four(engine, acme, store)
    fresh_before = await fingerprint(engine, fresh.run_id)
    ws = acme.context.workspace_id

    result = await enforce(retention_engine, store)

    assert result.mode == "enforce" and result.failed == 0 and result.halted is None
    # Oldest: everything is gone, files included, run record last.
    left = await fingerprint(engine, gone.run_id)
    assert all(left[k][0] == 0 for k in (*RUN_KEYED, "runs", "outbox_jobs"))
    assert not any(await files_exist(store, gone))
    # Past the trace tier: detail and files gone, the record stays and says so.
    after = await fingerprint(engine, expired_trace.run_id)
    assert all(after[k][0] == 0 for k in (*RUN_KEYED, "outbox_jobs")) and after["runs"][0] == 1
    assert not any(await files_exist(store, expired_trace))
    assert (
        await scalar(engine, "SELECT trace_state FROM runs WHERE id = :r", r=expired_trace.run_id)
        == "expired"
    )
    # Past only the artifacts tier: files and rows gone; events, spans, derived rows remain.
    files_only = await fingerprint(engine, expired_files.run_id)
    assert files_only["artifacts"][0] == 0 and files_only["events"][0] == 3
    assert files_only["spans"][0] == 2 and files_only["runs"][0] == 1
    assert not any(await files_exist(store, expired_files))
    assert (
        await scalar(engine, "SELECT trace_state FROM runs WHERE id = :r", r=expired_files.run_id)
        == "available"
    )
    # Not expired: byte-identical, files included.
    assert await fingerprint(engine, fresh.run_id) == fresh_before
    assert all(await files_exist(store, fresh))
    # Tombstones: one per run and tier removed, written by the same step as the delete.
    assert await tombstones(engine) == sorted(
        [
            (gone.run_id, "artifacts"), (gone.run_id, "trace"), (gone.run_id, "run"),
            (expired_trace.run_id, "artifacts"), (expired_trace.run_id, "trace"),
            (expired_files.run_id, "artifacts"),
        ],
        key=lambda x: (x[0], x[1]),
    )  # fmt: skip
    # Nothing is left half done: the progress table is empty and the second tick is a no-op.
    assert await scalar(engine, "SELECT count(*) FROM retention_run_progress") == 0
    again = await enforce(retention_engine, store)
    assert (again.deleted_runs, again.deleted_events, again.failed) == (0, 0, 0)
    assert len(await tombstones(engine)) == 6
    assert ws  # silence unused


async def test_enforce_reports_what_a_dry_run_predicted(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    await seed_four(engine, acme, store)
    await run_tick(
        retention_engine, settings(), budget_seconds=60, keep_reports=50, now=lambda: NOW
    )
    await enforce(retention_engine, store)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT tier, mode, runs, events, artifact_bytes, row_count, truncated "
                    "FROM retention_reports WHERE workspace_id IS NOT NULL "
                    "AND tier IN ('artifacts', 'trace', 'run') ORDER BY mode, tier"
                )
            )
        ).all()
    by = {(r.mode, r.tier): r for r in rows}
    for tier in ("artifacts", "trace", "run"):
        dry, real = by[("dry_run", tier)], by[("enforce", tier)]
        assert (dry.runs, dry.events, dry.artifact_bytes, dry.row_count) == (
            real.runs,
            real.events,
            real.artifact_bytes,
            real.row_count,
        ), tier
        assert real.truncated is False


async def test_the_cutoff_is_exclusive_and_forever_means_never(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    at = await seed_run(engine, acme, ended=days_ago(30), store=store, artifact_sizes=(5,))
    over = await seed_run(
        engine, acme, ended=NOW - timedelta(days=30, seconds=1), store=store, artifact_sizes=(7,)
    )
    await enforce(retention_engine, store)
    assert all(await files_exist(store, at)) and not any(await files_exist(store, over))
    # `forever` on every tier: nothing at all is removed, however old.
    old = await seed_run(engine, acme, ended=days_ago(5000), store=store, artifact_sizes=(9,))
    ws = acme.context.workspace_id
    async with engine.begin() as conn:
        await conn.execute(
            insert(t.retention_policies).values(
                workspace_id=ws,
                updated_by="test",
                artifacts_forever=True,
                trace_forever=True,
                run_forever=True,
                audit_forever=True,
            )
        )
    before = await fingerprint(engine, old.run_id)
    await enforce(retention_engine, store)
    assert await fingerprint(engine, old.run_id) == before and all(await files_exist(store, old))


async def test_one_workspaces_policy_never_deletes_anothers_data(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    globex = await make_tenant(engine, "globex")
    mine = await seed_run(engine, acme, ended=days_ago(20), store=store, artifact_sizes=(5,))
    theirs = await seed_run(engine, globex, ended=days_ago(20), store=store, artifact_sizes=(5,))
    async with engine.begin() as conn:  # acme keeps files only 7 days; globex keeps the default
        await conn.execute(
            insert(t.retention_policies).values(
                workspace_id=acme.context.workspace_id,
                updated_by="test",
                artifacts_days=7,
                trace_days=8,
                run_days=9,
            )
        )
    theirs_before = await fingerprint(engine, theirs.run_id)
    await enforce(retention_engine, store)
    assert (await fingerprint(engine, mine.run_id))["runs"][0] == 0  # acme's run is past 9 days
    assert not any(await files_exist(store, mine))
    assert await fingerprint(engine, theirs.run_id) == theirs_before
    assert all(await files_exist(store, theirs))


# ---------------------------------------------------------------- the quota counter


async def test_the_artifact_usage_counter_is_decremented_by_exactly_the_deleted_bytes(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    await seed_run(engine, acme, ended=days_ago(400), store=store, artifact_sizes=(100, 250))
    await seed_run(engine, acme, ended=days_ago(40), store=store, artifact_sizes=(1000,))
    await seed_run(engine, acme, ended=days_ago(5), store=store, artifact_sizes=(7, 3))
    project = next(iter(acme.project_uuids.values()))
    counter = "SELECT bytes_stored FROM project_artifact_usage WHERE project_id = :p"
    assert await scalar(engine, counter, p=project) == 100 + 250 + 1000 + 7 + 3
    await enforce(retention_engine, store)
    assert await scalar(engine, counter, p=project) == 10  # only the fresh run's bytes remain
    await enforce(retention_engine, store)  # a rerun subtracts nothing twice
    assert await scalar(engine, counter, p=project) == 10


async def test_orphan_artifacts_expire_by_their_own_upload_time(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    """KI-040: an artifact whose run never arrived is removed once older than the artifacts tier
    (by upload time); a younger one, and one whose run exists, follow the normal rules."""
    from abb_api.artifacts.service import storage_key

    acme = await make_tenant(engine, "acme")
    ws, project = acme.context.workspace_id, next(iter(acme.project_uuids.values()))
    ids = {}
    async with engine.begin() as conn:
        for name, age in (("old", 31), ("young", 29)):
            artifact_id = uuid.uuid4()
            key = storage_key(ws, artifact_id, b"\x01" * 32)
            await store.put(key, b"orphan")
            ids[name] = key
            await conn.execute(
                insert(t.artifacts).values(
                    workspace_id=ws,
                    id=artifact_id,
                    run_id=uuid.uuid4(),
                    project_id=project,
                    artifact_type="stdout",
                    storage_uri=key,
                    size_bytes=6,
                    created_at=days_ago(age),
                )
            )
        await conn.execute(
            text(
                "INSERT INTO project_artifact_usage (workspace_id, project_id, bytes_stored) "
                "VALUES (:w, :p, 12)"
            ),
            {"w": ws, "p": project},
        )
    await enforce(retention_engine, store)
    with pytest.raises(Exception, match="missing"):
        await store.read(ids["old"], 0, 1)
    assert await store.read(ids["young"], 0, 6) == b"orphan"
    assert await scalar(engine, "SELECT count(*) FROM artifacts") == 1
    assert await scalar(engine, "SELECT bytes_stored FROM project_artifact_usage") == 6
    assert await tombstones(engine) == []  # no run to name


async def test_an_invalid_storage_key_does_not_block_the_row_from_going(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    await seed_run(engine, acme, ended=days_ago(400), artifact_sizes=(5,))  # storage_uri file://x
    result = await enforce(retention_engine, store)
    assert result.failed == 0
    assert await scalar(engine, "SELECT count(*) FROM artifacts") == 0


# ---------------------------------------------------------------- a run that is not the oldest


async def test_a_run_updated_after_it_was_selected_is_left_alone(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    """A run that stopped being expired between selection and marking is not touched."""
    acme = await make_tenant(engine, "acme")
    run = await seed_run(engine, acme, ended=days_ago(100), store=store, artifact_sizes=(5,))

    async def revive(point: str) -> None:
        if point == "artifacts":  # after the artifacts tier, before the trace tier selects it
            async with engine.begin() as conn:
                await conn.execute(
                    text("UPDATE runs SET updated_at = :n, completed_at = :n WHERE id = :r"),
                    {"n": NOW, "r": run.run_id},
                )

    await enforce(retention_engine, store, hook=revive)
    after = await fingerprint(engine, run.run_id)
    assert after["events"][0] == 3 and after["runs"][0] == 1  # detail and record kept
    assert after["artifacts"][0] == 0  # the artifacts tier had already acted
    assert await scalar(engine, "SELECT trace_state FROM runs") == "available"
