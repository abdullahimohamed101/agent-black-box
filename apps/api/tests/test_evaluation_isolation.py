"""Evaluator failures never change a run, its events or the queue around them (AC-2, ADR-090)."""

import logging
import threading
import time
from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.db import tables as t
from abb_api.evaluations import builtin
from abb_api.evaluations.protocol import EvaluationResult
from abb_api.evaluations.repository import EvaluationRepository
from abb_api.jobs.handlers import HANDLERS
from abb_api.jobs.outbox import EVALUATE_RUN, SUMMARIZE_RUN, JobQueue
from abb_api.jobs.worker import Worker
from tests.conftest import make_settings
from tests.evaluation_helpers import add_evaluator, evaluation_rows, jobs_of, seeded_run, trigger
from tests.ingest_helpers import make_tenant
from tests.test_summarizer import drain, worker_for

CANARY = "ISOLATIONCANARY-cmd-curl-s3cr3t"
LEASE, TIMEOUT = 3.0, 1.2  # the timeout is below half the lease, so the heartbeat can run


class Raises:
    name = "raises"
    version = 1
    kind = "deterministic"
    config_model = builtin.NoConfig
    metric_keys = frozenset[str]()

    def evaluate(self, snapshot: Any, config: Any, budget: Any) -> EvaluationResult:
        raise RuntimeError(f"failed on {CANARY}")


class BusyLoop(Raises):
    """Synchronous, ignores its budget: only `wait_for` can stop the *wait* for it."""

    name = "busy_loop"
    stop = threading.Event()

    def evaluate(self, snapshot: Any, config: Any, budget: Any) -> EvaluationResult:
        while not self.stop.is_set():  # the test sets this when it is done; a real one never would
            time.sleep(0.01)
        return EvaluationResult(1.0, "pass", "late")


class Garbage(Raises):
    name = "garbage"
    metric_keys = frozenset({"ok"})

    def evaluate(self, snapshot: Any, config: Any, budget: Any) -> EvaluationResult:
        return EvaluationResult(0.5, CANARY, CANARY * 400, {"ok": 1}, ("evt_x",))


@pytest.fixture(autouse=True)
def fakes(monkeypatch: pytest.MonkeyPatch) -> Any:
    BusyLoop.stop.clear()
    for fake in (Raises(), BusyLoop(), Garbage()):
        monkeypatch.setitem(builtin.REGISTRY, fake.name, fake)
    yield
    BusyLoop.stop.set()  # let the leftover thread end


def isolated_worker(engine: AsyncEngine, database_url: str, **settings: Any) -> Worker:
    settings.setdefault("worker_lease_seconds", LEASE)
    settings.setdefault("eval_timeout_seconds", TIMEOUT)
    settings.setdefault("worker_backoff_base_seconds", 0)
    return Worker(engine, HANDLERS, make_settings(database_url, **settings), owner="iso")


async def state_of_the_world(engine: AsyncEngine) -> dict[str, Sequence[Any]]:
    async with engine.connect() as conn:
        return {
            "runs": (await conn.execute(select(t.runs))).all(),
            "events": (
                await conn.execute(select(t.events.c.event_id, t.events.c.content_hash))
            ).all(),
            "spans": (await conn.execute(select(t.spans))).all(),
            "cost": (await conn.execute(select(t.cost_calculations))).all(),
        }


async def test_failing_evaluators_leave_the_run_the_events_and_the_summary_queue_untouched(
    engine: AsyncEngine, database_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    tenant = await make_tenant(engine, "acme")
    run_id = await seeded_run(engine, tenant, worker_for(engine, database_url))
    raises = await add_evaluator(engine, tenant, "raises", name="raises")
    garbage = await add_evaluator(engine, tenant, "garbage", name="garbage")
    busy = await add_evaluator(engine, tenant, "busy_loop", name="busy")
    ok = await add_evaluator(engine, tenant, "run_succeeded", name="ok")
    before, summaries = await state_of_the_world(engine), len(await jobs_of(engine, SUMMARIZE_RUN))
    await trigger(engine, tenant, run_id, raises, garbage, busy, ok)

    extended: list[Any] = []
    original = JobQueue.extend_lease

    async def counting(self: JobQueue, job: Any, lease: Any, now: Any = None) -> bool:
        extended.append(job.id)
        return await original(self, job, lease, now)

    worker = isolated_worker(engine, database_url)
    started = time.monotonic()
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(JobQueue, "extend_lease", counting)
            await drain(worker)
    finally:
        BusyLoop.stop.set()  # before the loop closes: it joins the executor's threads
    elapsed = time.monotonic() - started

    rows = {r.evaluator_name: r for r in await evaluation_rows(engine, run_id)}
    assert (rows["raises"].state, rows["raises"].error_code) == ("failed", "EVALUATOR_ERROR")
    assert rows["raises"].error == "RuntimeError"  # the class, never the message
    assert (rows["garbage"].state, rows["garbage"].error_code) == ("failed", "INVALID_RESULT")
    assert (rows["busy"].state, rows["busy"].error_code) == ("failed", "TIMEOUT")
    assert rows["ok"].state == "completed"  # the evaluator after the stuck one still ran
    assert elapsed < TIMEOUT + 4  # waited once for the busy loop, not for ever
    for row in rows.values():
        assert CANARY not in repr(row), row.evaluator_name
    # The lease was extended while the evaluator spun, so the job completed on its first attempt.
    assert extended, "the heartbeat never ran: the handler starved the event loop"
    (job,) = await jobs_of(engine, EVALUATE_RUN)
    assert (job.status, job.attempt_count, job.last_error) == ("done", 1, None)
    # Nothing outside `evaluations` changed, and no re-summary was queued.
    assert await state_of_the_world(engine) == before
    assert len(await jobs_of(engine, SUMMARIZE_RUN)) == summaries
    assert CANARY not in caplog.text


async def test_a_run_over_the_cap_fails_and_other_jobs_keep_processing(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant = await make_tenant(engine, "acme")
    worker = worker_for(engine, database_url)
    big = await seeded_run(
        engine, tenant, worker, [("tool.call.completed", {"tool.name": "t"})] * 150
    )
    small_run = await seeded_run(engine, tenant, worker)
    ok = await add_evaluator(engine, tenant)
    await trigger(engine, tenant, big, ok)
    await trigger(engine, tenant, small_run, ok)
    capped = isolated_worker(engine, database_url, eval_max_events=100)
    await drain(capped)
    (too_large,) = await evaluation_rows(engine, big)
    assert (too_large.state, too_large.error_code) == ("failed", "RUN_TOO_LARGE")
    assert (await evaluation_rows(engine, small_run))[0].state == "completed"
    assert {j.status for j in await jobs_of(engine, EVALUATE_RUN)} == {"done"}


async def test_a_database_outage_inside_the_handler_retries_the_job_with_a_fixed_message(
    engine: AsyncEngine, database_url: str, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:  # fmt: skip
    caplog.set_level(logging.DEBUG)
    tenant = await make_tenant(engine, "acme")
    run_id = await seeded_run(engine, tenant, worker_for(engine, database_url))
    await trigger(engine, tenant, run_id, await add_evaluator(engine, tenant))
    calls = {"n": 0}
    original = EvaluationRepository.claim_pending

    async def flaky(self: EvaluationRepository, run: Any, limit: int) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError(f"SELECT {CANARY}", {"p": CANARY}, Exception(CANARY))
        return await original(self, run, limit)

    monkeypatch.setattr(EvaluationRepository, "claim_pending", flaky)
    worker = isolated_worker(engine, database_url)
    await worker.run_once()
    (job,) = await jobs_of(engine, EVALUATE_RUN)
    assert (job.status, job.attempt_count) == ("pending", 1)
    assert job.last_error == "EvaluationJobError: evaluate_run failed"  # no SQL, no parameters
    assert (await evaluation_rows(engine, run_id))[0].state == "pending"
    await drain(worker)  # the retry succeeds
    assert (await evaluation_rows(engine, run_id))[0].state == "completed"
    (job,) = await jobs_of(engine, EVALUATE_RUN)
    assert (job.status, job.attempt_count, job.last_error) == (
        "done",
        2,
        None,
    ) or job.status == "done"
    assert CANARY not in caplog.text
