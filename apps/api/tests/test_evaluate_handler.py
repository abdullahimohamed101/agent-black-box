"""The evaluate_run handler: states, immutability, bounds, contained failures (ADR-090)."""

import logging
import uuid
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.db import tables as t
from abb_api.evaluations import builtin
from abb_api.evaluations.protocol import EvaluationResult, EvaluatorError
from abb_api.jobs.outbox import EVALUATE_RUN, SUMMARIZE_RUN
from tests.conftest import make_settings
from tests.evaluation_helpers import (
    add_evaluator,
    evaluation_rows,
    jobs_of,
    seeded_run,
    trigger,
)
from tests.ingest_helpers import make_tenant
from tests.test_summarizer import drain, worker_for

FAILED_TESTS = (
    "shell.command.completed",
    {"shell.command": "pytest", "test.framework": "pytest", "test.failed": 2, "test.total": 5},
)


class Boom:
    """A test evaluator that raises with a value in its message."""

    name = "boom"
    version = 1
    kind = "deterministic"
    config_model = builtin.NoConfig
    metric_keys = frozenset[str]()

    def evaluate(self, snapshot: Any, config: Any, budget: Any) -> EvaluationResult:
        raise ValueError("SECRET-VALUE-IN-MESSAGE")


class Garbage(Boom):
    name = "garbage"
    metric_keys = frozenset({"ok"})

    def evaluate(self, snapshot: Any, config: Any, budget: Any) -> EvaluationResult:
        return EvaluationResult(
            score=7.0, label="SECRET-GARBAGE", explanation="x", metrics={"ok": 1}
        )


class Templated(Boom):
    name = "templated"

    def evaluate(self, snapshot: Any, config: Any, budget: Any) -> EvaluationResult:
        raise EvaluatorError("The check found 3 problems.")


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    for fake in (Boom(), Garbage(), Templated()):
        monkeypatch.setitem(builtin.REGISTRY, fake.name, fake)


async def test_a_pending_evaluation_completes_with_score_label_explanation_and_evidence(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant = await make_tenant(engine, "acme")
    worker = worker_for(engine, database_url)
    run_id = await seeded_run(engine, tenant, worker, [FAILED_TESTS])
    ok = await add_evaluator(engine, tenant, "run_succeeded", name="ok")
    tests = await add_evaluator(engine, tenant, "tests_passed", name="tests")
    ids = await trigger(engine, tenant, run_id, ok, tests)
    assert {r.state for r in await evaluation_rows(engine, run_id)} == {"pending"}
    await drain(worker)
    rows = {r.evaluator_name: r for r in await evaluation_rows(engine, run_id)}
    assert {r.id for r in rows.values()} == set(ids)
    assert (rows["ok"].state, rows["ok"].score, rows["ok"].label) == ("completed", 1.0, "pass")
    assert (rows["tests"].state, rows["tests"].score, rows["tests"].label) == (
        "completed",
        0.0,
        "fail",
    )
    assert rows["tests"].metrics == {"tests_failed": 2, "tests_total": 5}
    assert len(rows["tests"].evidence_event_ids) == 1 and rows["tests"].evidence_event_ids[
        0
    ].startswith("evt_")
    assert rows["tests"].started_at and rows["tests"].completed_at and rows["tests"].error is None
    (job,) = await jobs_of(engine, EVALUATE_RUN)
    assert job.status == "done"


async def test_a_requeued_job_for_terminal_rows_changes_nothing(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant = await make_tenant(engine, "acme")
    worker = worker_for(engine, database_url)
    run_id = await seeded_run(engine, tenant, worker)
    evaluator = await add_evaluator(engine, tenant)
    await trigger(engine, tenant, run_id, evaluator)
    await drain(worker)
    (before,) = await evaluation_rows(engine, run_id)
    # A redelivery (a lost lease, an operator requeue) of the same work.
    async with engine.begin() as conn:
        await conn.execute(update(t.outbox_jobs).values(status="pending", attempt_count=0))
    await drain(worker)
    (after,) = await evaluation_rows(engine, run_id)
    assert after == before
    # A re-run is a new row; the first is untouched.
    await trigger(engine, tenant, run_id, evaluator)
    await drain(worker)
    rows = await evaluation_rows(engine, run_id)
    assert len(rows) == 2 and rows[0] == before and rows[1].state == "completed"


async def test_evaluator_failures_are_results_with_codes_and_no_values(
    engine: AsyncEngine, database_url: str, fakes: None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    tenant = await make_tenant(engine, "acme")
    worker = worker_for(engine, database_url)
    run_id = await seeded_run(engine, tenant, worker)
    evaluators = [
        await add_evaluator(engine, tenant, impl, name=impl)
        for impl in ("boom", "garbage", "templated", "run_succeeded")
    ]
    await trigger(engine, tenant, run_id, *evaluators)
    await drain(worker)
    rows = {r.evaluator_name: r for r in await evaluation_rows(engine, run_id)}
    assert (rows["boom"].state, rows["boom"].error_code, rows["boom"].error) == (
        "failed",
        "EVALUATOR_ERROR",
        "ValueError",
    )
    assert (rows["garbage"].state, rows["garbage"].error_code) == ("failed", "INVALID_RESULT")
    assert "SECRET" not in (rows["garbage"].error or "")
    assert rows["templated"].error == "EvaluatorError: The check found 3 problems."
    assert rows["run_succeeded"].state == "completed"  # a neighbour is unaffected
    (job,) = await jobs_of(engine, EVALUATE_RUN)
    assert job.status == "done" and job.last_error is None and job.attempt_count == 1
    assert "SECRET" not in caplog.text and "SECRET" not in repr(rows)


async def test_a_run_above_the_event_cap_fails_every_evaluation_with_run_too_large(
    engine: AsyncEngine, database_url: str
) -> None:
    tenant = await make_tenant(engine, "acme")
    worker = worker_for(engine, database_url)
    many = [("tool.call.completed", {"tool.name": "t"})] * 150
    run_id = await seeded_run(engine, tenant, worker, many)
    evaluator = await add_evaluator(engine, tenant)
    await trigger(engine, tenant, run_id, evaluator)
    small = worker_with(engine, database_url, eval_max_events=100)
    await drain(small)
    (row,) = await evaluation_rows(engine, run_id)
    assert (row.state, row.error_code) == ("failed", "RUN_TOO_LARGE")


def worker_with(engine: AsyncEngine, database_url: str, **settings: Any) -> Any:
    from abb_api.jobs.handlers import HANDLERS
    from abb_api.jobs.worker import Worker

    return Worker(engine, HANDLERS, make_settings(database_url, **settings), owner="small")


async def test_a_run_that_vanished_fails_its_pending_evaluations(
    engine: AsyncEngine, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The narrow race: retention removes the run between the trigger and the handler's read."""
    tenant = await make_tenant(engine, "acme")
    worker = worker_for(engine, database_url)
    run_id = await seeded_run(engine, tenant, worker)
    evaluator = await add_evaluator(engine, tenant)
    await trigger(engine, tenant, run_id, evaluator)

    async def gone(self: Any, run: uuid.UUID) -> None:
        return None

    monkeypatch.setattr("abb_api.runs.queries.RunQueries.get", gone)
    await drain(worker)
    (row,) = await evaluation_rows(engine, run_id)
    assert (row.state, row.error_code) == ("failed", "RUN_NOT_FOUND")


async def test_the_handler_leaves_the_run_its_events_and_its_summary_queue_alone(
    engine: AsyncEngine, database_url: str, fakes: None
) -> None:
    tenant = await make_tenant(engine, "acme")
    worker = worker_for(engine, database_url)
    run_id = await seeded_run(engine, tenant, worker)
    evaluators = [
        await add_evaluator(engine, tenant, impl, name=impl) for impl in ("boom", "run_succeeded")
    ]
    async with engine.connect() as conn:
        run_before = (await conn.execute(select(t.runs))).all()
        events_before = (
            await conn.execute(select(t.events.c.event_id, t.events.c.content_hash))
        ).all()
        costs_before = (await conn.execute(select(t.cost_calculations))).all()
    summaries_before = len(await jobs_of(engine, SUMMARIZE_RUN))
    await trigger(engine, tenant, run_id, *evaluators)
    await drain(worker)
    async with engine.connect() as conn:
        assert (await conn.execute(select(t.runs))).all() == run_before
        assert (
            await conn.execute(select(t.events.c.event_id, t.events.c.content_hash))
        ).all() == events_before
        assert (await conn.execute(select(t.cost_calculations))).all() == costs_before
    assert len(await jobs_of(engine, SUMMARIZE_RUN)) == summaries_before


async def test_more_than_one_jobs_worth_continues_in_a_follow_up_job(
    engine: AsyncEngine, database_url: str
) -> None:
    from abb_api.evaluations.job import PER_JOB

    tenant = await make_tenant(engine, "acme")
    worker = worker_for(engine, database_url)
    run_id = await seeded_run(engine, tenant, worker)
    evaluators = [await add_evaluator(engine, tenant, name=f"e{n}") for n in range(PER_JOB + 3)]
    await trigger(engine, tenant, run_id, *evaluators)
    await drain(worker)
    states = [r.state for r in await evaluation_rows(engine, run_id)]
    assert (states.count("completed"), states.count("pending")) == (PER_JOB, 3)
    follow_up = [j for j in await jobs_of(engine, EVALUATE_RUN) if j.status == "pending"]
    assert len(follow_up) == 1  # one coalesced follow-up, delayed
    async with engine.begin() as conn:
        await conn.execute(
            update(t.outbox_jobs)
            .where(t.outbox_jobs.c.status == "pending")
            .values(available_at=func.now())
        )
    await drain(worker)
    assert {r.state for r in await evaluation_rows(engine, run_id)} == {"completed"}


async def test_start_only_moves_a_pending_row_and_never_touches_a_terminal_one(
    engine: AsyncEngine, database_url: str
) -> None:
    """`UPDATE ... WHERE state = 'pending'` is the only way out of pending: a row that became
    terminal (or is already running) between the claim and the start is skipped, not rewritten
    and not an error."""
    from abb_api.evaluations.repository import EvaluationRepository

    tenant = await make_tenant(engine, "acme")
    worker = worker_for(engine, database_url)
    run_id = await seeded_run(engine, tenant, worker)
    evaluator = await add_evaluator(engine, tenant, "run_succeeded", name="ok")
    (pending,) = await trigger(engine, tenant, run_id, evaluator)
    async with engine.begin() as conn:
        repo = EvaluationRepository(conn, tenant.context)
        assert await repo.start(pending) is True
        assert await repo.start(pending) is False  # already running
    await drain(worker)  # the handler finds no pending row; the running one stays as it is
    (still,) = await evaluation_rows(engine, run_id)
    assert still.state == "running"
    async with engine.begin() as conn:
        repo = EvaluationRepository(conn, tenant.context)
        assert await repo.complete(
            pending,
            score=1.0,
            label="pass",
            explanation="x",
            metrics={},
            evidence_event_ids=[],
            metadata={},
        )
    async with engine.begin() as conn:
        assert await EvaluationRepository(conn, tenant.context).start(pending) is False
        assert (
            await EvaluationRepository(conn, tenant.context).fail(pending, code="X", error="y")
            is False
        )
    (done,) = await evaluation_rows(engine, run_id)
    assert (done.state, done.label) == ("completed", "pass")
