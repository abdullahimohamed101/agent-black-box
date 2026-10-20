"""The outbox queue and worker: claiming, leases, retries, dead letters, shutdown."""

import asyncio
import inspect
import logging
import uuid
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.db import tables as t
from abb_api.jobs.outbox import Job, JobQueue, backoff_seconds, oldest_first
from abb_api.jobs.worker import Worker
from tests.api_fixtures import Tick
from tests.conftest import make_settings
from tests.factories import make_workspace

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
LEASE = timedelta(seconds=60)
LEASE_SECONDS = 0.9  # injected-clock lease tests: real heartbeat every 0.3 s
STEP = timedelta(seconds=0.3)  # the injected clock moves one third of a lease per heartbeat


def due(ago: float = 0) -> datetime:
    """A time that is already past on the database's clock whatever its drift from this host's.

    The database runs in a VM whose clock drifts and steps by seconds under load (KI-309: measured
    up to 13 s); a job written as "now" by the host can be "not yet due" there. Hence the hour.
    """
    return datetime.now(UTC) - timedelta(hours=1, seconds=ago)


async def add_job(
    conn: AsyncConnection, ws: uuid.UUID, *, key: str | None = None, **values: Any
) -> uuid.UUID:
    job_id = uuid.uuid4()
    row: dict[str, Any] = {
        "id": job_id,
        "job_type": "test",
        "workspace_id": ws,
        "dedupe_key": key or str(job_id),
        "status": "pending",
        "available_at": T0,
    }
    row.update(values)
    await conn.execute(insert(t.outbox_jobs).values(**row))
    return job_id


async def job_row(engine: AsyncEngine, job_id: uuid.UUID) -> Any:
    async with engine.connect() as conn:
        return (await conn.execute(select(t.outbox_jobs).where(t.outbox_jobs.c.id == job_id))).one()


async def workspace(engine: AsyncEngine) -> uuid.UUID:
    async with engine.begin() as conn:
        return await make_workspace(conn)


def make_worker(
    engine: AsyncEngine, database_url: str, handlers: dict[str, Any], clock: Tick, **settings: Any
) -> Worker:
    return Worker(
        engine, handlers, make_settings(database_url, **settings), clock=clock, owner="w1"
    )


# ------------------------------------------------------------------ claiming


async def test_claim_takes_due_pending_jobs_oldest_first_and_marks_them_running(
    engine: AsyncEngine,
) -> None:
    ws = await workspace(engine)
    async with engine.begin() as conn:
        late = await add_job(conn, ws, available_at=T0 - timedelta(minutes=1))
        early = await add_job(conn, ws, available_at=T0 - timedelta(minutes=5))
        await add_job(conn, ws, available_at=T0 + timedelta(minutes=5))  # not due yet
        await add_job(conn, ws, status="done")
    async with engine.begin() as conn:
        jobs = await JobQueue(conn).claim(owner="w1", limit=10, lease=LEASE, now=T0)
    assert [j.id for j in jobs] == [early, late]
    assert all(j.attempt_count == 1 and j.lease_owner == "w1" for j in jobs)
    row = await job_row(engine, early)
    assert row.status == "running" and row.lease_expires_at == T0 + LEASE


async def test_claim_respects_the_limit(engine: AsyncEngine) -> None:
    ws = await workspace(engine)
    async with engine.begin() as conn:
        for _ in range(5):
            await add_job(conn, ws)
    async with engine.begin() as conn:
        assert len(await JobQueue(conn).claim(owner="w", limit=3, lease=LEASE, now=T0)) == 3


async def test_concurrent_claimers_get_disjoint_jobs_without_blocking(engine: AsyncEngine) -> None:
    """While one claimer's transaction is still open, another skips its rows instead of waiting."""
    ws = await workspace(engine)
    async with engine.begin() as conn:
        ids = {await add_job(conn, ws) for _ in range(40)}

    first_claimed: list[Job] = []
    second_claimed: list[Job] = []
    holding, release = asyncio.Event(), asyncio.Event()

    async def first() -> None:
        async with engine.begin() as conn:
            first_claimed.extend(
                await JobQueue(conn).claim(owner="a", limit=20, lease=LEASE, now=T0)
            )
            holding.set()
            await release.wait()  # keep the transaction (and its row locks) open

    async def second() -> None:
        await holding.wait()
        async with engine.begin() as conn:
            second_claimed.extend(
                await JobQueue(conn).claim(owner="b", limit=20, lease=LEASE, now=T0)
            )
        release.set()

    await asyncio.wait_for(asyncio.gather(first(), second()), timeout=10)  # blocking would hang
    a, b = {j.id for j in first_claimed}, {j.id for j in second_claimed}
    assert len(a) == len(b) == 20 and not a & b and a | b == ids


async def test_an_expired_lease_is_reclaimed_and_a_live_one_is_not(engine: AsyncEngine) -> None:
    ws = await workspace(engine)
    async with engine.begin() as conn:
        crashed = await add_job(
            conn,
            ws,
            status="running",
            lease_owner="dead",
            lease_expires_at=T0 - timedelta(seconds=1),
            attempt_count=1,
        )
        busy = await add_job(
            conn,
            ws,
            status="running",
            lease_owner="alive",
            lease_expires_at=T0 + timedelta(seconds=30),
            attempt_count=1,
        )
    async with engine.begin() as conn:
        jobs = await JobQueue(conn).claim(owner="w2", limit=10, lease=LEASE, now=T0)
    assert [j.id for j in jobs] == [crashed]
    assert jobs[0].attempt_count == 2 and jobs[0].lease_owner == "w2"
    assert (await job_row(engine, busy)).lease_owner == "alive"


async def test_only_the_lease_holder_can_complete(engine: AsyncEngine) -> None:
    ws = await workspace(engine)
    async with engine.begin() as conn:
        await add_job(conn, ws)
    async with engine.begin() as conn:
        (job,) = await JobQueue(conn).claim(owner="w1", limit=1, lease=LEASE, now=T0)
    impostor = Job(**{**job.__dict__, "lease_owner": "someone-else"})
    async with engine.begin() as conn:
        assert not await JobQueue(conn).complete(impostor, T0)
        assert await JobQueue(conn).complete(job, T0)
        assert not await JobQueue(conn).complete(job, T0)  # already done


def test_backoff_is_exponential_and_capped() -> None:
    assert [backoff_seconds(n, 5, 300) for n in range(1, 9)] == [5, 10, 20, 40, 80, 160, 300, 300]


# ------------------------------------------------------------------ the worker


async def test_a_successful_job_commits_its_writes_and_is_marked_done(
    engine: AsyncEngine, database_url: str
) -> None:
    ws = await workspace(engine)
    async with engine.begin() as conn:
        job_id = await add_job(conn, ws)
    calls: list[uuid.UUID] = []

    async def handler(job: Job, conn: AsyncConnection) -> None:
        calls.append(job.id)
        await conn.execute(insert(t.workspaces).values(id=uuid.uuid4(), name="made", slug="made"))

    worker = make_worker(engine, database_url, {"test": handler}, Tick(T0))
    assert await worker.run_once() == 1
    assert calls == [job_id]
    row = await job_row(engine, job_id)
    assert row.status == "done" and row.lease_owner is None
    async with engine.connect() as conn:
        assert (
            await conn.execute(select(func.count()).where(t.workspaces.c.slug == "made"))
        ).scalar_one() == 1
    assert await worker.run_once() == 0  # nothing left


async def test_a_failing_job_rolls_back_and_retries_after_a_backoff(
    engine: AsyncEngine, database_url: str
) -> None:
    ws = await workspace(engine)
    async with engine.begin() as conn:
        job_id = await add_job(conn, ws)

    async def handler(job: Job, conn: AsyncConnection) -> None:
        await conn.execute(insert(t.workspaces).values(id=uuid.uuid4(), name="x", slug="half-done"))
        raise ValueError("nope")

    clock = Tick(T0)
    worker = make_worker(
        engine, database_url, {"test": handler}, clock, worker_backoff_base_seconds=5
    )
    await worker.run_once()
    row = await job_row(engine, job_id)
    assert row.status == "pending" and row.attempt_count == 1
    assert row.available_at == T0 + timedelta(seconds=5)
    assert "ValueError: nope" in row.last_error
    async with engine.connect() as conn:  # the handler's partial write was rolled back
        assert (
            await conn.execute(select(func.count()).where(t.workspaces.c.slug == "half-done"))
        ).scalar_one() == 0
    assert await worker.run_once() == 0  # not claimable until the backoff has passed
    clock.now = T0 + timedelta(seconds=5)
    assert await worker.run_once() == 1


async def test_jobs_are_dead_lettered_after_the_maximum_attempts(
    engine: AsyncEngine, database_url: str
) -> None:
    ws = await workspace(engine)
    async with engine.begin() as conn:
        job_id = await add_job(conn, ws)
    attempts = 0

    async def handler(job: Job, conn: AsyncConnection) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError(f"failure {attempts}")

    clock = Tick(T0)
    worker = make_worker(engine, database_url, {"test": handler}, clock, worker_max_attempts=3)
    for _ in range(6):
        await worker.run_once()
        clock.now += timedelta(hours=1)  # always past the backoff
    row = await job_row(engine, job_id)
    assert attempts == 3  # no fourth attempt
    assert row.status == "dead_letter" and row.attempt_count == 3 and "failure 3" in row.last_error


async def test_a_job_with_no_handler_is_dead_lettered_at_once(
    engine: AsyncEngine, database_url: str
) -> None:
    ws = await workspace(engine)
    async with engine.begin() as conn:
        job_id = await add_job(conn, ws, job_type="mystery")
    await make_worker(engine, database_url, {}, Tick(T0)).run_once()
    row = await job_row(engine, job_id)
    assert row.status == "dead_letter" and "no handler" in row.last_error


async def test_a_worker_that_lost_its_lease_changes_nothing(
    engine: AsyncEngine, database_url: str
) -> None:
    ws = await workspace(engine)
    async with engine.begin() as conn:
        job_id = await add_job(conn, ws)

    async def handler(job: Job, conn: AsyncConnection) -> None:
        await conn.execute(
            insert(t.workspaces).values(id=uuid.uuid4(), name="x", slug="stale-write")
        )
        async with engine.begin() as other:  # another worker reclaims the job meanwhile
            await other.execute(
                update(t.outbox_jobs).where(t.outbox_jobs.c.id == job.id).values(lease_owner="w2")
            )

    await make_worker(engine, database_url, {"test": handler}, Tick(T0)).run_once()
    row = await job_row(engine, job_id)
    assert row.status == "running" and row.lease_owner == "w2"  # untouched by the loser
    async with engine.connect() as conn:
        assert (
            await conn.execute(select(func.count()).where(t.workspaces.c.slug == "stale-write"))
        ).scalar_one() == 0


async def test_a_failed_job_is_superseded_when_newer_work_is_already_queued(
    engine: AsyncEngine, database_url: str
) -> None:
    ws = await workspace(engine)
    async with engine.begin() as conn:
        job_id = await add_job(conn, ws, key="run-1")

    async def handler(job: Job, conn: AsyncConnection) -> None:
        async with engine.begin() as other:  # new events queue a pending job for the same run
            await add_job(other, ws, key="run-1")
        raise RuntimeError("boom")

    await make_worker(engine, database_url, {"test": handler}, Tick(T0)).run_once()
    row = await job_row(engine, job_id)
    assert row.status == "done" and "superseded" in row.last_error
    async with engine.connect() as conn:
        pending = (
            await conn.execute(select(func.count()).where(t.outbox_jobs.c.status == "pending"))
        ).scalar_one()
    assert pending == 1  # the newer job remains to redo the work


async def test_operators_can_requeue_dead_letters(engine: AsyncEngine) -> None:
    ws = await workspace(engine)
    async with engine.begin() as conn:
        dead = await add_job(
            conn, ws, key="a", status="dead_letter", attempt_count=5, last_error="x"
        )
        blocked = await add_job(conn, ws, key="b", status="dead_letter", attempt_count=5)
        await add_job(conn, ws, key="b")  # newer pending work already exists for b
    async with engine.begin() as conn:
        assert await JobQueue(conn).requeue_dead_letters(T0, dead) == 1
        assert await JobQueue(conn).requeue_dead_letters(T0, blocked) == 0  # no clash, no crash
    row = await job_row(engine, dead)
    assert row.status == "pending" and row.attempt_count == 0 and row.last_error is None


# ------------------------------------------------------------------ the loop


async def test_the_loop_processes_new_jobs_and_stops_promptly(
    engine: AsyncEngine, database_url: str
) -> None:
    ws = await workspace(engine)
    handled: list[uuid.UUID] = []

    async def handler(job: Job, conn: AsyncConnection) -> None:
        handled.append(job.id)

    stop = asyncio.Event()
    worker = Worker(
        engine,
        {"test": handler},
        make_settings(database_url, worker_poll_interval_seconds=0.05),
        owner="loop",
    )
    task = asyncio.create_task(worker.run_forever(stop))
    await asyncio.sleep(0.2)  # idle for a few polls
    async with engine.begin() as conn:
        job_id = await add_job(conn, ws, available_at=due())
    for _ in range(100):
        if handled:
            break
        await asyncio.sleep(0.05)
    assert handled == [job_id]
    stop.set()
    await asyncio.wait_for(task, timeout=2)


async def test_the_loop_survives_an_error_and_keeps_working(
    engine: AsyncEngine, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = await workspace(engine)
    handled: list[uuid.UUID] = []

    async def handler(job: Job, conn: AsyncConnection) -> None:
        handled.append(job.id)

    worker = Worker(
        engine, {"test": handler}, make_settings(database_url, worker_poll_interval_seconds=0.05)
    )
    real = Worker.run_once
    state = {"calls": 0}

    async def flaky(self: Worker) -> int:
        state["calls"] += 1
        if state["calls"] == 1:
            raise ConnectionError("database restarted")
        return await real(self)

    monkeypatch.setattr(Worker, "run_once", flaky)
    async with engine.begin() as conn:
        await add_job(conn, ws, available_at=due())
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_forever(stop))
    for _ in range(100):
        if handled:
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert len(handled) == 1


async def test_the_worker_process_starts_processes_jobs_and_exits_cleanly_on_sigterm(
    engine: AsyncEngine, database_url: str
) -> None:
    """The real entrypoint, as a subprocess: claims a job, handles SIGTERM, exits 0."""
    import os
    import signal
    import sys

    from tests.conftest import API_DIR

    ws = await workspace(engine)
    run_id = uuid.uuid4()
    async with engine.begin() as conn:  # a summarize job for a run that does not exist: a no-op
        job_id = await add_job(
            conn, ws, job_type="summarize_run", payload={"run_id": str(run_id)},
            available_at=due(),
        )  # fmt: skip
    env = {
        **os.environ,
        "ABB_DATABASE_URL": database_url,
        "ABB_WORKER_POLL_INTERVAL_SECONDS": "0.05",
        "ABB_LOG_LEVEL": "INFO",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "abb_api.worker",
        cwd=API_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        for _ in range(100):  # wait for the other process to handle the job
            if (await job_row(engine, job_id)).status == "done":
                break
            await asyncio.sleep(0.1)
        assert (await job_row(engine, job_id)).status == "done"
        process.send_signal(signal.SIGTERM)
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
    finally:
        if process.returncode is None:
            process.kill()
    output = stdout.decode()
    assert process.returncode == 0
    assert "worker started" in output and "worker stopped" in output


# ------------------------------------------------------------------ leases and poison pills


async def until(condition: Callable[[], Any], *, ceiling: float = 30.0) -> None:
    """Wait for an observed fact. The ceiling only bounds a hang; it is not a timing assertion."""
    async with asyncio.timeout(ceiling):
        while not await _truthy(condition()):  # noqa: ASYNC110 - polls database state
            await asyncio.sleep(0.02)


async def _truthy(value: Any) -> bool:
    return bool(await value) if inspect.isawaitable(value) else bool(value)


@pytest.fixture
def extensions(monkeypatch: pytest.MonkeyPatch) -> Counter[uuid.UUID]:
    """How many times each job's lease was extended (successfully) by a heartbeat."""
    counts: Counter[uuid.UUID] = Counter()
    real = JobQueue.extend_lease

    async def counting(
        self: JobQueue, job: Job, lease: timedelta, now: datetime | None = None
    ) -> bool:
        extended = await real(self, job, lease, now)
        if extended:
            counts[job.id] += 1
        return extended

    monkeypatch.setattr(JobQueue, "extend_lease", counting)
    return counts


async def advance_while_extended(
    clock: Tick,
    extensions: Counter[uuid.UUID],
    job_ids: list[uuid.UUID],
    *,
    until_after: datetime,
    gave_up: Callable[[], bool],
) -> None:
    """Move the injected clock past `until_after` in small steps, one heartbeat per step.

    The workers schedule by this clock, so a lease expires only when the test advances it, never
    because the machine is slow. Each step waits until every job's lease was extended again, so
    the lease always stays ahead of the clock by the heartbeat's own doing: with no heartbeat the
    wait never ends (the test fails on its ceiling) and, if a lease did lapse, `gave_up` ends it
    early so the assertions can report the duplicate run.
    """
    while clock.now <= until_after:
        seen = {job_id: extensions[job_id] for job_id in job_ids}
        clock.now += STEP

        def extended_or_gave_up(seen: dict[uuid.UUID, int] = seen) -> bool:
            return gave_up() or all(extensions[j] > seen[j] for j in job_ids)

        try:
            await until(extended_or_gave_up, ceiling=20)
        except TimeoutError:
            pytest.fail("no heartbeat extended the lease of a job that is still running")
        if gave_up():
            return


async def test_a_slow_job_keeps_its_lease_and_runs_exactly_once(
    engine: AsyncEngine, database_url: str, extensions: Counter[uuid.UUID]
) -> None:
    """Found by review: without a heartbeat a job outliving its lease ran 8 times (limit 5).

    Time is injected (`Worker(clock=...)`): the handler is held while the test moves the clock
    past the lease the claim granted, one heartbeat at a time, with idle workers polling. Nothing
    depends on how fast the machine is (KI-309: the old sleeps did, and the database's own clock
    stepped by seconds under load).
    """
    clock = Tick(T0)
    ws = await workspace(engine)
    async with engine.begin() as conn:
        job_id = await add_job(conn, ws, job_type="slow", available_at=T0 - timedelta(seconds=1))
    runs: list[str] = []
    release = asyncio.Event()

    async def slow(job: Job, conn: AsyncConnection) -> None:
        runs.append(job.lease_owner)
        await release.wait()

    settings = make_settings(
        database_url, worker_lease_seconds=LEASE_SECONDS, worker_poll_interval_seconds=0.05
    )
    workers = [
        Worker(engine, {"slow": slow}, settings, clock=clock, owner=f"w{i}") for i in range(3)
    ]
    polls = {w.owner: 0 for w in workers}
    for w in workers:
        w.run_once = _counting(w, polls)  # type: ignore[method-assign]
    stop = asyncio.Event()
    tasks = [asyncio.create_task(w.run_forever(stop)) for w in workers]
    try:
        await until(lambda: runs)
        original = (await job_row(engine, job_id)).lease_expires_at
        await advance_while_extended(
            clock, extensions, [job_id], until_after=original, gave_up=lambda: len(runs) > 1
        )
        # The clock is past the original lease. Give the idle workers a few more polls (a count
        # of their loops, not a duration) to try to take the job.
        idle = [w.owner for w in workers if w.owner != runs[0]]  # the runner is inside its job
        floor = {owner: polls[owner] for owner in idle}
        await until(lambda: len(runs) > 1 or all(polls[o] >= floor[o] + 3 for o in idle))
    finally:
        release.set()
        await until(lambda: _is_done(engine, job_id))
        stop.set()
        await asyncio.gather(*tasks)
    row = await job_row(engine, job_id)
    assert len(runs) == 1 and row.status == "done" and row.attempt_count == 1
    assert clock.now > original and extensions[job_id] >= 3  # it outlived its first lease


def _counting(worker: Worker, polls: dict[str, int]) -> Callable[[], Any]:
    """`worker.run_once` that counts how many times the worker looked for work."""
    real = worker.run_once

    async def counted() -> int:
        polls[worker.owner] += 1
        return int(await real())

    return counted


async def _is_done(engine: AsyncEngine, job_id: uuid.UUID) -> bool:
    return bool((await job_row(engine, job_id)).status == "done")


async def test_jobs_claimed_in_the_same_batch_keep_their_leases_while_an_earlier_one_runs(
    engine: AsyncEngine, database_url: str, extensions: Counter[uuid.UUID]
) -> None:
    """Review P2-2: a worker runs its batch one job after another and used to extend only the
    running job's lease; the claimed one behind a slow job (a delivery tick) expired and was run
    a second time by another worker.

    The first job is held while the injected clock moves past the lease the second one was
    granted; the batch keeper must extend it meanwhile, with a second worker polling.
    """
    clock = Tick(T0)
    ws = await workspace(engine)
    async with engine.begin() as conn:
        ids = [
            await add_job(conn, ws, job_type="slow", available_at=T0 - timedelta(seconds=2 - n))
            for n in range(2)
        ]  # the first id is the older, so it runs first
    runs: list[str] = []
    release = asyncio.Event()

    async def slow(job: Job, conn: AsyncConnection) -> None:
        runs.append(str(job.id))
        await release.wait()

    settings = make_settings(
        database_url,
        worker_lease_seconds=LEASE_SECONDS,
        worker_poll_interval_seconds=0.05,
        worker_batch_size=2,
    )
    first = Worker(engine, {"slow": slow}, settings, clock=clock, owner="first")
    other = Worker(engine, {"slow": slow}, settings, clock=clock, owner="other")
    polls = {"other": 0}
    other.run_once = _counting(other, polls)  # type: ignore[method-assign]
    claiming = asyncio.create_task(first.run_once())  # takes both and runs them in turn
    stop = asyncio.Event()
    watching: asyncio.Task[None] | None = None
    try:
        await until(lambda: runs)  # the first job runs; the second waits, claimed, behind it
        assert runs == [str(ids[0])]
        original = (await job_row(engine, ids[1])).lease_expires_at
        watching = asyncio.create_task(other.run_forever(stop))
        await advance_while_extended(
            clock, extensions, ids, until_after=original, gave_up=lambda: len(runs) > 1
        )
        floor = polls["other"]
        # It had its chances to steal the job (or did: a second run, which the assertions report).
        await until(lambda: len(runs) > 1 or polls["other"] >= floor + 3)
    finally:
        release.set()
        await claiming
        stop.set()
        if watching is not None:
            await watching
    assert sorted(runs) == sorted(str(i) for i in ids)  # each exactly once
    assert clock.now > original and extensions[ids[1]] >= 3  # kept alive while it waited
    for job_id in ids:
        row = await job_row(engine, job_id)
        assert row.status == "done" and row.attempt_count == 1


async def test_the_heartbeat_stops_with_the_job_and_reports_a_lost_lease(
    engine: AsyncEngine, database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="abb_api.jobs.worker")
    ws = await workspace(engine)
    async with engine.begin() as conn:
        await add_job(conn, ws, job_type="quick", available_at=due())

    async def quick(job: Job, conn: AsyncConnection) -> None:
        return None

    worker = Worker(
        engine, {"quick": quick}, make_settings(database_url, worker_lease_seconds=0.3), owner="w"
    )
    await worker.run_once()
    leftover = [task for task in asyncio.all_tasks() if "_keep_lease" in repr(task.get_coro())]
    assert leftover == []  # no heartbeat outlives its job
    async with engine.begin() as conn:  # extending a lease the caller does not hold is refused
        (job,) = await _claim_fresh(conn, ws)
        impostor = Job(**{**job.__dict__, "lease_owner": "someone-else"})
        assert not await JobQueue(conn).extend_lease(impostor, LEASE)
        assert await JobQueue(conn).extend_lease(job, LEASE)

    # A lease taken over mid-run (set directly in the database, not waited out): the heartbeat
    # reports it and stops, and the job is left to its new owner.
    async with engine.begin() as conn:
        stolen = await add_job(conn, ws, job_type="stolen", available_at=due())

    async def takeover(job: Job, conn: AsyncConnection) -> None:
        if job.id != stolen:
            return
        async with engine.begin() as other:
            await other.execute(
                update(t.outbox_jobs)
                .where(t.outbox_jobs.c.id == stolen)
                .values(lease_owner="thief", lease_expires_at=datetime.now(UTC) + LEASE)
            )
        await until(lambda: any("lease lost while running" in r.message for r in caplog.records))

    thief_worker = Worker(
        engine,
        {"stolen": takeover},
        make_settings(database_url, worker_lease_seconds=0.3),
        owner="w3",
    )
    await thief_worker.run_once()
    row = await job_row(engine, stolen)
    assert row.status == "running" and row.lease_owner == "thief"  # not completed by the loser
    assert any("lease lost; leaving the job" in r.message for r in caplog.records)
    leftover = [task for task in asyncio.all_tasks() if "_keep_lease" in repr(task.get_coro())]
    assert leftover == []


async def _claim_fresh(conn: AsyncConnection, ws: uuid.UUID) -> list[Job]:
    await add_job(conn, ws, available_at=due(1))
    return await JobQueue(conn).claim(owner="w2", limit=1, lease=LEASE)


async def test_a_job_that_keeps_killing_its_worker_is_dead_lettered_not_reclaimed_forever(
    engine: AsyncEngine,
) -> None:
    ws = await workspace(engine)
    expired = T0 - timedelta(seconds=1)
    async with engine.begin() as conn:
        poison = await add_job(
            conn,
            ws,
            status="running",
            lease_owner="dead",
            lease_expires_at=expired,
            attempt_count=5,
        )
        retryable = await add_job(
            conn,
            ws,
            status="running",
            lease_owner="dead",
            lease_expires_at=expired,
            attempt_count=4,
        )
    async with engine.begin() as conn:
        claimed = await JobQueue(conn).claim(
            owner="w", limit=10, lease=LEASE, now=T0, max_attempts=5
        )
    assert [j.id for j in claimed] == [retryable]  # its fifth and last attempt
    row = await job_row(engine, poison)
    assert row.status == "dead_letter" and "lease expired after 5 attempts" in row.last_error
    assert row.lease_owner is None


def test_claimed_rows_are_put_back_in_oldest_first_order_whatever_order_they_arrive() -> None:
    """UPDATE ... RETURNING may return rows in any order (it did, once, in the gate)."""
    import random
    from types import SimpleNamespace

    rows = [
        SimpleNamespace(id=uuid.UUID(int=n), available_at=T0 - timedelta(minutes=m), created_at=T0)
        for n, m in enumerate(
            (3, 9, 1, 7, 5, 2, 8, 4, 6, 7)
        )  # two share a due time: id breaks the tie
    ]
    expected = [r.id for r in oldest_first(rows)]
    assert [(T0 - r.available_at).seconds // 60 for r in oldest_first(rows)] == sorted(
        [(T0 - r.available_at).seconds // 60 for r in rows], reverse=True
    )
    for seed in range(20):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert [r.id for r in oldest_first(shuffled)] == expected
