"""The scheduled-check chain (ADR-150 amendment 1, review P2-4): other alert jobs re-arm it, and
its lock order cannot deadlock with a rule edit."""

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from abb_api.alerts import jobs
from abb_api.alerts import model as m
from abb_api.alerts.jobs import ensure_sweep
from abb_api.alerts.repository import RuleRepository
from abb_api.jobs.context import reset_job_settings, set_job_settings
from abb_api.jobs.outbox import ALERT_SWEEP, Job, OutboxRepository
from tests.alerts_helpers import post_run, seed_destination, seed_rule
from tests.api_fixtures import NOW, Api
from tests.approvals.helpers import ask, publish
from tests.conftest import make_settings


async def pending_sweeps(api: Api) -> int:
    async with api.engine.connect() as conn:
        return int(
            (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM outbox_jobs "
                        "WHERE job_type = :t AND status = 'pending'"
                    ),
                    {"t": ALERT_SWEEP},
                )
            ).scalar_one()
        )


async def test_alert_traffic_restarts_a_dead_scheduled_check_chain(api: Api) -> None:
    destination = await seed_destination(api.engine, api.tenant.context)
    await seed_rule(
        api.engine, api.tenant.context, [destination], trigger=m.APPROVAL_EXPIRING,
        filters={"expiring_lead_seconds": 600},
    )  # fmt: skip
    await seed_rule(api.engine, api.tenant.context, [destination], name="fails")
    assert await pending_sweeps(api) == 0  # seeded rows start no timer: the chain is "dead"
    await post_run(api, "FAILED")
    await api.drain()  # summary -> evaluate_alerts -> deliver_alerts (held in tests)
    assert await pending_sweeps(api) == 1
    await api.drain()
    assert await pending_sweeps(api) == 1  # coalesced, not one per job


async def test_without_a_scheduled_rule_alert_jobs_start_no_timer(api: Api) -> None:
    destination = await seed_destination(api.engine, api.tenant.context)
    await seed_rule(api.engine, api.tenant.context, [destination], name="fails")
    await post_run(api, "FAILED")
    await api.drain()
    assert await pending_sweeps(api) == 0


async def test_a_sweep_and_an_edit_of_the_same_rule_cannot_deadlock(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API edit holds the rule's row and then inserts the sweep key; the sweep must not hold
    that key's pending insert while it waits for the rule's row."""
    await publish(api)
    destination = await seed_destination(api.engine, api.tenant.context)
    rule_id = await seed_rule(
        api.engine, api.tenant.context, [destination], trigger=m.APPROVAL_EXPIRING,
        filters={"expiring_lead_seconds": 900},
    )  # fmt: skip
    await ask(api)  # waiting at NOW, valid for 15 minutes: the sweep will want the rule's lock
    monkeypatch.setattr(jobs, "system_clock", lambda: NOW + timedelta(minutes=2))
    sweep_started, edit_holds = asyncio.Event(), asyncio.Event()
    real_sweep = jobs.sweep

    async def sweep_after_the_edit_took_its_lock(*args: Any, **kwargs: Any) -> Any:
        sweep_started.set()
        await asyncio.wait_for(edit_holds.wait(), 10)
        await asyncio.sleep(0.3)  # the edit is about to insert the sweep key
        return await real_sweep(*args, **kwargs)

    monkeypatch.setattr(jobs, "sweep", sweep_after_the_edit_took_its_lock)
    settings = make_settings("postgresql+asyncpg://unused")
    job = Job(
        id=uuid.uuid4(), job_type=ALERT_SWEEP, workspace_id=api.tenant.context.workspace_id,
        dedupe_key=str(api.tenant.context.workspace_id), payload={}, attempt_count=1,
        lease_owner="test",
    )  # fmt: skip

    async def the_sweep() -> None:
        token = set_job_settings(settings)
        try:
            async with api.engine.begin() as conn:
                await jobs.alert_sweep(job, conn)
        finally:
            reset_job_settings(token)

    async def the_edit() -> None:
        await asyncio.wait_for(sweep_started.wait(), 10)
        async with api.engine.begin() as conn:
            await RuleRepository(conn, api.tenant.context).update(
                rule_id, updated_by="user:test", name="renamed"
            )
            edit_holds.set()
            await asyncio.sleep(0.1)
            await ensure_sweep(OutboxRepository(conn, api.tenant.context), api.tenant.context)

    results = await asyncio.wait_for(
        asyncio.gather(the_sweep(), the_edit(), return_exceptions=True), 30
    )
    deadlocks = [r for r in results if isinstance(r, DBAPIError)]
    assert not deadlocks, deadlocks
    assert results == [None, None]
    assert await pending_sweeps(api) == 1  # the edit's job; the sweep's successor coalesced
