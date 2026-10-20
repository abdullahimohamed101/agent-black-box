"""KI-097 cadence: one analysis per run per minimum interval, trailing, no floor once it ends."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from abb_event_schema.ids import to_uuid
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.core.config import Settings
from abb_api.db import tables as t
from abb_api.findings.job import detect_enqueue_delay
from abb_api.jobs import handlers
from abb_api.jobs.context import reset_job_settings, set_job_settings
from abb_api.jobs.outbox import DETECT_FINDINGS
from abb_api.tenancy import TenantContext
from tests.api_fixtures import Api
from tests.conftest import make_settings
from tests.test_findings_api import fixture_wires, post_wires, run_id_of

NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=UTC)


def delay(**kw: object) -> timedelta:
    args: dict[str, object] = {
        "base_seconds": 5.0,
        "event_count": 10,
        "min_interval_seconds": 10.0,
        "computed_at": NOW - timedelta(seconds=3),
        "db_now": NOW,
        "terminal": False,
    }
    args.update(kw)
    return detect_enqueue_delay(**args)  # type: ignore[arg-type]


def test_a_recent_analysis_pushes_the_next_one_to_the_interval() -> None:
    assert delay() == timedelta(seconds=7)  # 10 - 3, more than the 5 s base
    assert delay(computed_at=NOW - timedelta(seconds=9)) == timedelta(seconds=5)  # base wins


def test_an_old_or_missing_analysis_pays_only_the_normal_delay() -> None:
    assert delay(computed_at=NOW - timedelta(seconds=60)) == timedelta(seconds=5)
    assert delay(computed_at=None) == timedelta(seconds=5)


def test_a_finished_run_is_never_throttled() -> None:
    assert delay(terminal=True) == timedelta(seconds=5)
    assert delay(terminal=True, base_seconds=0.0) == timedelta(0)


def test_zero_disables_the_floor_and_a_clock_ahead_of_the_database_is_harmless() -> None:
    assert delay(min_interval_seconds=0.0) == timedelta(seconds=5)
    assert delay(computed_at=NOW + timedelta(seconds=30)) == timedelta(seconds=10)  # capped


def test_the_interval_comes_from_the_environment_and_defaults_to_ten_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ABB_FINDINGS_MIN_INTERVAL_SECONDS", raising=False)
    assert Settings(database_url="x").findings_min_interval_seconds == 10.0
    monkeypatch.setenv("ABB_FINDINGS_MIN_INTERVAL_SECONDS", "0")
    assert Settings(database_url="x").findings_min_interval_seconds == 0.0
    monkeypatch.setenv("ABB_FINDINGS_MIN_INTERVAL_SECONDS", "-1")
    with pytest.raises(ValueError, match="findings_min_interval_seconds"):
        Settings(database_url="x")


# ------------------------------------------------------------------ against the database clock


async def analysed_run(api: Api) -> uuid.UUID:
    await post_wires(api, fixture_wires("looping"))
    await api.drain()
    return to_uuid(run_id_of("looping"))


async def pending_detect(engine: AsyncEngine) -> list[object]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            select(t.outbox_jobs).where(
                t.outbox_jobs.c.job_type == DETECT_FINDINGS, t.outbox_jobs.c.status == "pending"
            )
        )
        return list(rows)


async def enqueue_at_db_age(
    api: Api, run_id: uuid.UUID, *, age_seconds: float, terminal: bool, interval: float = 10.0
) -> float:
    """Pretend the last analysis completed `age_seconds` ago on the database clock, enqueue, and
    return how far in the future (database clock) the pending job is due."""
    settings = make_settings("postgresql+asyncpg://x:y@localhost/db", findings_delay_seconds=0.0)
    settings = settings.model_copy(update={"findings_min_interval_seconds": interval})
    token = set_job_settings(settings)
    try:
        async with api.engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM outbox_jobs WHERE job_type = :j"), {"j": DETECT_FINDINGS}
            )
            await conn.execute(
                text("UPDATE run_findings SET computed_at = now() - make_interval(secs => :s)"),
                {"s": age_seconds},
            )
            await handlers.enqueue_detect(
                conn, TenantContext(api.tenant.context.workspace_id), run_id, 10, terminal=terminal
            )
            due = await conn.scalar(
                select(func.extract("epoch", t.outbox_jobs.c.available_at - func.now())).where(
                    t.outbox_jobs.c.job_type == DETECT_FINDINGS
                )
            )
    finally:
        reset_job_settings(token)
    assert due is not None
    return float(due)


async def test_three_seconds_after_an_analysis_the_next_is_at_least_seven_seconds_away(
    api: Api,
) -> None:
    run_id = await analysed_run(api)
    due = await enqueue_at_db_age(api, run_id, age_seconds=3.0, terminal=False)
    assert 6.9 <= due <= 7.1


async def test_a_stale_analysis_waits_only_the_normal_delay(api: Api) -> None:
    run_id = await analysed_run(api)
    assert await enqueue_at_db_age(api, run_id, age_seconds=120.0, terminal=False) < 0.1


async def test_a_run_that_ended_is_analysed_without_the_floor(api: Api) -> None:
    run_id = await analysed_run(api)
    assert await enqueue_at_db_age(api, run_id, age_seconds=1.0, terminal=True) < 0.1


async def test_the_floor_can_be_switched_off(api: Api) -> None:
    run_id = await analysed_run(api)
    due = await enqueue_at_db_age(api, run_id, age_seconds=1.0, terminal=False, interval=0.0)
    assert due < 0.1


async def test_the_end_of_a_run_pulls_a_throttled_pending_job_forward(api: Api) -> None:
    """The trailing job is already queued 8 s away when the terminal event arrives: the final
    analysis must not wait for it."""
    run_id = await analysed_run(api)
    assert await enqueue_at_db_age(api, run_id, age_seconds=2.0, terminal=False) > 7.0
    settings = make_settings("postgresql+asyncpg://x:y@localhost/db", findings_delay_seconds=0.0)
    token = set_job_settings(settings.model_copy(update={"findings_min_interval_seconds": 10.0}))
    try:
        async with api.engine.begin() as conn:
            await handlers.enqueue_detect(
                conn, TenantContext(api.tenant.context.workspace_id), run_id, 10, terminal=True
            )
            due = await conn.scalar(
                select(func.extract("epoch", t.outbox_jobs.c.available_at - func.now())).where(
                    t.outbox_jobs.c.job_type == DETECT_FINDINGS
                )
            )
    finally:
        reset_job_settings(token)
    assert due is not None and len(await pending_detect(api.engine)) == 1 and float(due) < 0.1


async def test_repeated_enqueues_coalesce_into_one_trailing_job(api: Api) -> None:
    run_id = await analysed_run(api)
    await enqueue_at_db_age(api, run_id, age_seconds=3.0, terminal=False)
    settings = make_settings("postgresql+asyncpg://x:y@localhost/db", findings_delay_seconds=0.0)
    token = set_job_settings(settings.model_copy(update={"findings_min_interval_seconds": 10.0}))
    try:
        for _ in range(5):
            async with api.engine.begin() as conn:
                await handlers.enqueue_detect(
                    conn,
                    TenantContext(api.tenant.context.workspace_id),
                    run_id,
                    10,
                    terminal=False,
                )
    finally:
        reset_job_settings(token)
    assert len(await pending_detect(api.engine)) == 1
