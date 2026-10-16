"""The order of deletion, the `deleting` marker, crash safety and the kill switch (ADR-160)."""

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.artifacts.store import LocalFsArtifactStore
from abb_api.retention.batches import HOOK_POINTS
from abb_api.runs.queries import RunQueries
from abb_api.tenancy import TenantContext
from tests.ingest_helpers import make_tenant
from tests.retention.enforce_helpers import (
    RUN_KEYED,
    enforce,
    enforcing,
    fingerprint,
    make_store,
    scalar,
    tombstones,
    truncate_all,
)
from tests.retention.helpers import days_ago, seed_run


@pytest.fixture
def store(tmp_path: Path) -> LocalFsArtifactStore:
    return make_store(tmp_path)


class Crash(BaseException):
    """A process death: not an Exception, so nothing in the job may swallow or clean up after it."""


async def view(engine: AsyncEngine, run_id: uuid.UUID, ws: uuid.UUID) -> dict[str, Any]:
    """What a concurrent reader (another connection) sees of the run right now."""
    async with engine.connect() as conn:
        record = await RunQueries(conn, TenantContext(ws)).get(run_id)
    prints = await fingerprint(engine, run_id)
    return {
        "state": record.trace_state if record else None,
        **{k: v[0] for k, v in prints.items()},
    }


async def test_steps_run_in_the_documented_order_and_a_reader_never_sees_a_broken_run(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    ws = acme.context.workspace_id
    run = await seed_run(
        engine, acme, ended=days_ago(400), store=store, full=True, artifact_sizes=(10, 20, 30),
        events=5, spans=3,
    )  # fmt: skip
    seen: list[tuple[str, dict[str, Any]]] = []

    async def watch(point: str) -> None:
        seen.append((point, await view(engine, run.run_id, ws)))

    await enforce(retention_engine, store, enforcing(retention_batch_size=2), hook=watch)
    by_point: dict[str, dict[str, Any]] = {}
    for point, v in seen:
        by_point.setdefault(point, v)  # the first time each point was reached
    # Files go first: at "artifacts" the rows are gone and the trace is still whole.
    assert by_point["artifacts"]["artifacts"] == 0 and by_point["artifacts"]["events"] == 5
    assert by_point["artifacts"]["state"] == "available"
    # The trace tier marks the run before touching anything, and readers see `deleting`.
    assert by_point["begun"]["state"] == "available"
    assert by_point["marked"]["state"] == "deleting" and by_point["marked"]["events"] == 5
    deleting = [v for p, v in seen if v["state"] == "deleting"]
    assert deleting, "a reader must be able to see the run as deleting"
    # The search entry goes before the events.
    assert by_point["search"]["run_search"] == 0 and by_point["search"]["events"] == 5
    # `expired` arrives together with the last detail row, never before.
    detail = next(v for p, v in seen if p == "detail")
    assert detail["state"] == "expired"
    assert all(detail[k] == 0 for k in RUN_KEYED)
    for point, v in seen:
        if v["state"] == "expired":
            assert all(v[k] == 0 for k in RUN_KEYED), point
        if v["state"] == "available" and v["events"] != 5:
            raise AssertionError(f"events shrank while the run still read as available ({point})")
    # The run record is deleted last.
    assert next(v for p, v in seen if p == "record")["runs"] == 0
    assert await scalar(engine, "SELECT count(*) FROM runs WHERE id = :r", r=run.run_id) == 0


async def test_a_partially_deleted_trace_is_marked_deleting_in_the_batches_between(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    acme = await make_tenant(engine, "acme")
    ws = acme.context.workspace_id
    run = await seed_run(engine, acme, ended=days_ago(100), events=7, spans=1)
    in_batches: list[dict[str, Any]] = []

    async def watch(point: str) -> None:
        if point == "detail_batch":
            in_batches.append(await view(engine, run.run_id, ws))

    await enforce(retention_engine, store, enforcing(retention_batch_size=2), hook=watch)
    assert len(in_batches) >= 3
    assert {v["state"] for v in in_batches} == {"deleting"}  # never `available` mid-deletion
    assert in_batches[0]["events"] == 5 and in_batches[-1]["events"] <= 1  # shrinking by batches
    assert await scalar(engine, "SELECT trace_state FROM runs") == "expired"


# ---------------------------------------------------------------- crash injection


async def seed_for_crash(engine: AsyncEngine, store: LocalFsArtifactStore):  # type: ignore[no-untyped-def]
    acme = await make_tenant(engine, "acme")
    runs = [
        await seed_run(
            engine, acme, ended=days_ago(400), store=store, full=True, artifact_sizes=(10, 20),
            events=2, spans=1,
        ),
        await seed_run(
            engine, acme, ended=days_ago(100), store=store, full=True, artifact_sizes=(40,),
            events=3, spans=2,
        ),
        await seed_run(engine, acme, ended=days_ago(40), store=store, artifact_sizes=(5,)),
    ]  # fmt: skip
    keep = await seed_run(engine, acme, ended=days_ago(3), store=store, artifact_sizes=(7,))
    return acme, runs, keep


async def final_state(
    engine: AsyncEngine, store: LocalFsArtifactStore, runs: list[Any], keep: Any
) -> dict[str, Any]:
    out: dict[str, Any] = {"tombstones": sorted(t[1] for t in await tombstones(engine))}
    out["progress"] = await scalar(engine, "SELECT count(*) FROM retention_run_progress")
    out["counter"] = await scalar(engine, "SELECT bytes_stored FROM project_artifact_usage")
    out["states"] = sorted(
        r[0] or "-" for r in await _rows(engine, "SELECT trace_state FROM runs ORDER BY started_at")
    )
    out["runs"] = await scalar(engine, "SELECT count(*) FROM runs")
    out["events"] = await scalar(engine, "SELECT count(*) FROM events")
    out["artifacts"] = await scalar(engine, "SELECT count(*) FROM artifacts")
    files = []
    for run in [*runs, keep]:
        files.append(len([k for k in run.artifact_keys if await _exists(store, k)]))
    out["files"] = sorted(files)
    return out


async def _exists(store: LocalFsArtifactStore, key: str) -> bool:
    try:
        await store.read(key, 0, 1)
        return True
    except Exception:
        return False


async def _rows(engine: AsyncEngine, sql: str) -> list[Any]:
    from sqlalchemy import text

    async with engine.connect() as conn:
        return [tuple(r) for r in await conn.execute(text(sql))]


# Exhaustive on purpose: it kills the procedure after EVERY hook call and reruns it, so it takes about 60 to 90 seconds and
# crossed the suite-wide 90 s limit on a loaded machine. Deterministic; only the budget is larger (as test_findings_job does).
@pytest.mark.timeout(600)
async def test_a_crash_after_every_single_step_is_completed_by_the_next_tick(
    engine: AsyncEngine, retention_engine: AsyncEngine, tmp_path: Path
) -> None:
    """Kill the procedure after each hook call in turn (every backend step and every batch), then
    run a clean tick: the end state is the one an uninterrupted run reaches, with no leftover
    row, no duplicate tombstone and no half-visible run."""
    store = make_store(tmp_path / "ref")
    _, runs, keep = await seed_for_crash(engine, store)
    calls: list[str] = []

    async def record(point: str) -> None:
        calls.append(point)

    await enforce(retention_engine, store, enforcing(), hook=record)
    reference = await final_state(engine, store, runs, keep)
    assert set(calls) >= set(HOOK_POINTS)  # the scenario reaches every hook point
    assert reference["progress"] == 0 and reference["states"] == [
        "available",
        "available",
        "expired",
    ]
    assert reference["tombstones"] == sorted(
        ["artifacts"] * 3 + ["trace"] * 2 + ["run"]
    )  # run 1: all three; run 2: artifacts+trace; run 3: artifacts

    for k in range(len(calls)):
        await truncate_all(engine)
        store = make_store(tmp_path / f"crash{k}")
        _, runs, keep = await seed_for_crash(engine, store)
        count = 0

        async def crash_at(point: str, k: int = k) -> None:
            nonlocal count
            if count == k:
                raise Crash(point)
            count += 1

        with pytest.raises(Crash):
            await enforce(retention_engine, store, enforcing(), hook=crash_at)
        # Whatever was reached, a reader finds a run that is whole or says its trace is going.
        for run in runs:
            state = [r for r in await _rows(engine, "SELECT id, trace_state FROM runs")]
            state = [(s,) for i, s in state if i == run.run_id]
            assert not state or state[0][0] in ("available", "deleting", "expired"), k
        await enforce(retention_engine, store, enforcing())
        assert await final_state(engine, store, runs, keep) == reference, f"crash after call {k}"


# ---------------------------------------------------------------- failures are per run


async def test_a_failing_artifact_store_fails_that_run_only_with_a_fixed_code_and_is_retried(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    from abb_api.artifacts.store import ArtifactStoreError

    acme = await make_tenant(engine, "acme")
    bad = await seed_run(engine, acme, ended=days_ago(400), store=store, artifact_sizes=(5,))
    good = await seed_run(engine, acme, ended=days_ago(400), store=store, artifact_sizes=(6,))

    class Flaky(LocalFsArtifactStore):
        broken = True

        async def delete(self, key: str) -> None:
            if self.broken and key == bad.artifact_keys[0]:
                raise ArtifactStoreError("disk exploded: secret-path-/etc/passwd")
            await super().delete(key)

    flaky = Flaky(store._root)
    result = await enforce(retention_engine, flaky)
    assert result.failed >= 1
    assert (await fingerprint(engine, good.run_id))["runs"][0] == 0  # the other run completed
    code = await scalar(
        engine,
        "SELECT failure_code FROM retention_run_progress WHERE run_id = :r AND tier = 'artifacts'",
        r=bad.run_id,
    )
    assert code == "artifact_store"  # a fixed code; the error text is nowhere
    assert "passwd" not in str(await _rows(engine, "SELECT * FROM retention_run_progress"))
    # The run stayed whole while its files could not go (artifacts tier: no `deleting` mark).
    assert (await fingerprint(engine, bad.run_id))["events"][0] == 3
    flaky.broken = False
    await enforce(retention_engine, flaky)
    assert (await fingerprint(engine, bad.run_id))["runs"][0] == 0
    assert await scalar(engine, "SELECT count(*) FROM retention_run_progress") == 0


async def test_attempts_are_bounded_and_retry_failed_gives_a_fresh_set(
    engine: AsyncEngine, retention_engine: AsyncEngine, store: LocalFsArtifactStore
) -> None:
    from abb_api.artifacts.store import ArtifactStoreError

    acme = await make_tenant(engine, "acme")
    bad = await seed_run(engine, acme, ended=days_ago(400), store=store, artifact_sizes=(5,))

    class Broken(LocalFsArtifactStore):
        async def delete(self, key: str) -> None:
            raise ArtifactStoreError("no")

    broken = Broken(store._root)
    cfg = enforcing(retention_max_attempts=2)
    for _ in range(4):
        await enforce(retention_engine, broken, cfg)
    attempts = await scalar(
        engine,
        "SELECT attempts FROM retention_run_progress WHERE run_id = :r AND tier = 'artifacts'",
        r=bad.run_id,
    )
    assert attempts == 2  # not retried beyond the bound
    from abb_api.retention.runner import reset_failed

    assert await reset_failed(retention_engine, 2) == 3  # artifacts, trace and run rows
    await enforce(retention_engine, store, cfg)
    assert (await fingerprint(engine, bad.run_id))["runs"][0] == 0
