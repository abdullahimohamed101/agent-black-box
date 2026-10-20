"""Scrape-time database gauges: right values, cached, and a down database never fails a scrape."""

import math
from datetime import timedelta
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.db import create_engine
from abb_api.jobs.outbox import DETECT_FINDINGS, SUMMARIZE_RUN, OutboxRepository
from abb_api.obs.catalogue import build_observability
from abb_api.obs.scrape import ScrapeSampler
from tests.conftest import UNREACHABLE_DATABASE_URL
from tests.ingest_helpers import make_tenant


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _seed_jobs(engine: AsyncEngine) -> None:
    tenant = await make_tenant(engine, "acme")
    async with engine.begin() as conn:
        outbox = OutboxRepository(conn, tenant.context)
        for n in range(3):
            await outbox.enqueue(job_type=SUMMARIZE_RUN, dedupe_key=f"s{n}", payload={})
        await outbox.enqueue(job_type=DETECT_FINDINGS, dedupe_key="d0", payload={})
        # Not due yet: must not be counted as waiting.
        await outbox.enqueue(
            job_type=DETECT_FINDINGS, dedupe_key="later", payload={}, delay=timedelta(hours=1)
        )
        # An old one, so the lag of its type is visible and larger than the others.
        await conn.execute(
            text(
                "UPDATE outbox_jobs SET available_at = now() - interval '90 seconds' "
                "WHERE dedupe_key = 's0'"
            )
        )
        await conn.execute(text("UPDATE outbox_jobs SET status = 'running' WHERE dedupe_key='s2'"))


def _count_statements(engine: AsyncEngine) -> list[str]:
    seen: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        if "set_config" not in statement:
            seen.append(statement)

    return seen


async def test_pending_jobs_and_lag_come_from_the_queue_by_type(
    engine: AsyncEngine, runtime_database_url: str
) -> None:
    await _seed_jobs(engine)
    obs = build_observability()
    runtime = create_engine(runtime_database_url)
    try:
        sampler = ScrapeSampler(lambda: runtime, obs.catalogue)
        await sampler.refresh()
    finally:
        await runtime.dispose()
    cat = obs.catalogue
    assert cat.abb_scrape_database_up.value() == 1
    assert cat.worker_jobs_pending.value(SUMMARIZE_RUN) == 2  # s0, s1 (s2 is running)
    assert cat.worker_jobs_pending.value(DETECT_FINDINGS) == 1  # d0; 'later' is not due
    assert cat.worker_job_lag_seconds.value(SUMMARIZE_RUN) >= 90
    assert 0 <= cat.worker_job_lag_seconds.value(DETECT_FINDINGS) < 90
    # Nothing else in the queue: no phantom series.
    assert {labels for labels, _ in _series(cat.worker_jobs_pending)} == {
        (SUMMARIZE_RUN,),
        (DETECT_FINDINGS,),
    }


def _series(gauge: Any) -> list[tuple[tuple[str, ...], float]]:
    return [(values, number) for _, values, number in gauge.collect()]


async def test_alerts_and_retention_gauges(engine: AsyncEngine, runtime_database_url: str) -> None:
    tenant = await make_tenant(engine, "acme")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO retention_reports (tick_id, workspace_id, tier, mode, started_at, "
                "finished_at) VALUES (gen_random_uuid(), :w, 'run', 'dry_run', "
                "now() - interval '1 hour', now() - interval '1 hour')"
            ),
            {"w": tenant.context.workspace_id},
        )
    obs = build_observability()
    runtime = create_engine(runtime_database_url)
    try:
        await ScrapeSampler(lambda: runtime, obs.catalogue).refresh()
    finally:
        await runtime.dispose()
    cat = obs.catalogue
    assert cat.alert_dead_letters.value() == 0
    assert cat.retention_mode.value("dry_run") == 1
    assert cat.retention_mode.value("enforce") == 0
    assert 3590 <= cat.retention_seconds_since_last_pass.value() <= 3700


async def test_no_retention_report_yet_is_nan_not_zero(
    engine: AsyncEngine, runtime_database_url: str
) -> None:
    obs = build_observability()
    runtime = create_engine(runtime_database_url)
    try:
        await ScrapeSampler(lambda: runtime, obs.catalogue).refresh()
    finally:
        await runtime.dispose()
    assert math.isnan(obs.catalogue.retention_seconds_since_last_pass.value())
    assert obs.catalogue.abb_scrape_database_up.value() == 1


async def test_a_second_scrape_within_the_window_runs_no_query_and_a_later_one_does(
    engine: AsyncEngine, runtime_database_url: str
) -> None:
    await _seed_jobs(engine)
    obs = build_observability()
    runtime = create_engine(runtime_database_url)
    clock = Clock()
    try:
        statements = _count_statements(runtime)
        sampler = ScrapeSampler(lambda: runtime, obs.catalogue, monotonic=clock)
        await sampler.refresh()
        first = len(statements)
        assert first == 4, "one statement per probe"
        clock.now += 14.9
        await sampler.refresh()
        await sampler.refresh()
        assert len(statements) == first, "inside the 15 s window no statement runs"
        assert sampler.samples == 1
        clock.now += 0.2
        await sampler.refresh()
        assert len(statements) == 2 * first
        assert sampler.samples == 2
    finally:
        await runtime.dispose()


async def test_a_database_that_goes_away_serves_the_cached_values_and_reports_down(
    engine: AsyncEngine, runtime_database_url: str
) -> None:
    await _seed_jobs(engine)
    obs = build_observability()
    good = create_engine(runtime_database_url)
    bad = create_engine(UNREACHABLE_DATABASE_URL)
    clock = Clock()
    chosen = {"engine": good}
    try:
        sampler = ScrapeSampler(lambda: chosen["engine"], obs.catalogue, monotonic=clock)
        await sampler.refresh()
        assert obs.catalogue.abb_scrape_database_up.value() == 1
        chosen["engine"] = bad
        clock.now += 20
        await sampler.refresh()  # must neither raise nor hang
        assert obs.catalogue.abb_scrape_database_up.value() == 0
        assert obs.catalogue.worker_jobs_pending.value(SUMMARIZE_RUN) == 2, "cached values served"
        # And one failed attempt per window: the next scrape inside it does not try again.
        attempts = sampler.samples
        await sampler.refresh()
        assert sampler.samples == attempts
        chosen["engine"] = good
        clock.now += 20
        await sampler.refresh()
        assert obs.catalogue.abb_scrape_database_up.value() == 1
    finally:
        await good.dispose()
        await bad.dispose()


async def test_no_engine_at_all_is_down_not_an_error() -> None:
    obs = build_observability()
    await ScrapeSampler(lambda: None, obs.catalogue).refresh()
    assert obs.catalogue.abb_scrape_database_up.value() == 0
