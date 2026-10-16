"""The grace period before a shortening is enforced, and the audit tier's own rules (ADR-159
amendments; review P2-2 and P2-3). Time comes from the injected clock, never from sleeping."""

from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.api_fixtures import WEB_ORIGIN, Api, Tick, build_api, person
from tests.conftest import make_settings
from tests.ingest_helpers import make_tenant
from tests.retention.enforce_helpers import enforce, enforcing, make_store, scalar
from tests.retention.helpers import NOW, days_ago, seed_run
from tests.retention.test_policy_api import MakeApi, audit, path, tiers

GRACE = timedelta(hours=24)


@pytest.fixture
async def make_api(
    database_url: str, runtime_database_url: str, engine: AsyncEngine
) -> AsyncIterator[MakeApi]:
    generators: list[Any] = []

    async def make(**settings: Any) -> Api:
        generator = build_api(
            database_url,
            engine,
            settings=make_settings(database_url, web_origin=WEB_ORIGIN, **settings),
            clock=Tick(NOW),
        )
        generators.append(generator)
        return await generator.__anext__()

    yield make
    for generator in generators:
        await generator.aclose()


def stamp(moment: Any) -> str:
    return str(moment.isoformat())


async def test_a_shortening_waits_for_the_grace_period_and_says_when(make_api: MakeApi) -> None:
    api = await make_api()
    owner = await person(api, "owner@acme.test", "OWNER")
    put = await api.client.put(path(api), json={"trace": 60}, headers=owner)
    assert put.status_code == 200, put.text
    body = put.json()
    trace = tiers(body)["trace"]
    assert trace["days"] == 60
    assert trace["pending"]["current_days"] == 90
    assert trace["pending"]["effective_at"].startswith(stamp(NOW + GRACE)[:19])
    assert body["grace_hours"] == 24 and body["audit_floor_days"] == 90
    (row,) = await audit(api.engine)
    assert row.details["old"] == "default" and row.details["new"] == 60
    assert row.details["effective_at"].startswith(stamp(NOW + GRACE)[:19])
    # After the effective time the shorter period is simply in force.
    api.clock.now = NOW + GRACE + timedelta(seconds=1)
    later = await person(api, "later@acme.test", "OWNER")  # the first session would be stale
    after = tiers((await api.client.get(path(api), headers=later)).json())["trace"]
    assert after["days"] == 60 and after["pending"] is None


async def test_a_lengthening_is_immediate(make_api: MakeApi) -> None:
    api = await make_api()
    owner = await person(api, "owner@acme.test", "OWNER")
    put = await api.client.put(path(api), json={"trace": 120}, headers=owner)
    assert tiers(put.json())["trace"]["pending"] is None
    (row,) = await audit(api.engine)
    assert row.details["effective_at"].startswith(stamp(NOW)[:19])


async def test_saving_the_previous_value_cancels_a_pending_shortening(make_api: MakeApi) -> None:
    api = await make_api()
    owner = await person(api, "owner@acme.test", "OWNER")
    await api.client.put(path(api), json={"trace": 60}, headers=owner)
    back = await api.client.put(path(api), json={"trace": 90}, headers=owner)
    trace = tiers(back.json())["trace"]
    assert trace["days"] == 90 and trace["pending"] is None


async def test_a_second_shortening_keeps_the_first_deadline_and_the_original_period(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    owner = await person(api, "owner@acme.test", "OWNER")
    first = tiers((await api.client.put(path(api), json={"trace": 60}, headers=owner)).json())
    api.clock.now = NOW + timedelta(hours=10)
    second = tiers((await api.client.put(path(api), json={"trace": 40}, headers=owner)).json())
    assert second["trace"]["days"] == 40
    assert second["trace"]["pending"]["effective_at"] == first["trace"]["pending"]["effective_at"]
    assert second["trace"]["pending"]["current_days"] == 90  # not 60: the original still applies


async def test_without_a_grace_period_a_shortening_applies_at_once(make_api: MakeApi) -> None:
    api = await make_api(retention_grace_hours=0)
    owner = await person(api, "owner@acme.test", "OWNER")
    put = await api.client.put(path(api), json={"trace": 60}, headers=owner)
    assert tiers(put.json())["trace"]["pending"] is None
    assert put.json()["grace_hours"] == 0


async def test_the_preview_shows_both_periods(make_api: MakeApi) -> None:
    api = await make_api()
    owner = await person(api, "owner@acme.test", "OWNER")
    preview = await api.client.get(path(api, "/preview?trace_days=60&tiers=trace"), headers=owner)
    (trace,) = preview.json()["tiers"]
    assert trace["days"] == 60 and trace["enforced_days"] == 90
    assert trace["effective_at"].startswith(stamp(NOW + GRACE)[:19])
    saved = await api.client.get(path(api, "/preview?tiers=trace"), headers=owner)
    (current,) = saved.json()["tiers"]
    assert current["days"] == 90 and current["enforced_days"] == 90
    assert current["effective_at"] is None


async def test_the_job_enforces_the_longer_period_until_the_effective_time(
    engine: AsyncEngine, retention_engine: AsyncEngine, tmp_path: Path
) -> None:
    store = make_store(tmp_path)
    acme = await make_tenant(engine, "acme")
    ws = acme.context.workspace_id
    run = await seed_run(engine, acme, ended=days_ago(60), events=2, spans=0, jobs=0)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO retention_policies (workspace_id, trace_days, trace_effective_at, "
                "updated_by) VALUES (:w, 30, :at, 'test')"
            ),
            {"w": ws, "at": NOW + timedelta(hours=12)},
        )

    async def state() -> str:
        return str(await scalar(engine, "SELECT trace_state FROM runs WHERE id = :r", r=run.run_id))

    await enforce(retention_engine, store, enforcing())
    assert await state() == "available"  # 60 days old, the shorter 30 is not in force yet
    await enforce(retention_engine, store, enforcing(), now=NOW + timedelta(hours=13))
    assert await state() == "expired"


async def test_only_an_owner_changes_the_audit_tier_and_it_has_its_own_minimum(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    admin = await person(api, "admin@acme.test", "ADMIN")
    owner = await person(api, "owner@acme.test", "OWNER")
    denied = await api.client.put(path(api), json={"audit": 120}, headers=admin)
    assert denied.status_code == 403
    assert await audit(api.engine) == []  # nothing was saved
    # An admin still changes the other tiers, and may re-send nothing for audit.
    assert (await api.client.put(path(api), json={"trace": 60}, headers=admin)).status_code == 200
    too_short = await api.client.put(path(api), json={"audit": 89}, headers=owner)
    assert too_short.status_code == 422
    assert too_short.json()["error"]["details"] == {"tier": "audit", "reason": "below_floor"}
    ok = await api.client.put(path(api), json={"audit": 90}, headers=owner)
    assert ok.status_code == 200 and tiers(ok.json())["audit"]["days"] == 90


async def test_the_audit_minimum_follows_the_environment(make_api: MakeApi) -> None:
    api = await make_api(retention_audit_floor_days=30)
    owner = await person(api, "owner@acme.test", "OWNER")
    ok = await api.client.put(path(api), json={"audit": 30}, headers=owner)
    assert ok.status_code == 200, ok.text
    assert ok.json()["audit_floor_days"] == 30


async def test_the_record_of_a_policy_change_outlives_the_audit_tier(
    engine: AsyncEngine, retention_engine: AsyncEngine, tmp_path: Path
) -> None:
    """Review P2-2(c): `retention.*` rows are exempt until 365 days old, whatever the policy."""
    store = make_store(tmp_path)
    acme = await make_tenant(engine, "acme")
    ws = acme.context.workspace_id
    async with engine.begin() as conn:
        for action, age in (
            ("old.thing", 200),
            ("retention.policy.update", 200),
            ("retention.delete.run", 200),
            ("retention.policy.update", 400),
        ):
            await conn.execute(
                text(
                    "INSERT INTO audit_log (workspace_id, actor_kind, actor_id, action, outcome, "
                    "occurred_at) VALUES (:w, 'user', 'u', :a, 'allowed', :t)"
                ),
                {"w": ws, "a": action, "t": NOW - timedelta(days=age)},
            )
        await conn.execute(
            text(
                "INSERT INTO retention_policies (workspace_id, audit_days, updated_by) "
                "VALUES (:w, 90, 'test')"
            ),
            {"w": ws},
        )
    await enforce(retention_engine, store, enforcing())
    async with engine.connect() as conn:
        left = sorted(
            (r[0], int((NOW - r[1]).days))
            for r in await conn.execute(
                text(
                    "SELECT action, occurred_at FROM audit_log WHERE workspace_id = :w "
                    "AND action NOT LIKE 'retention.delete.%'"
                ),
                {"w": ws},
            )
        )
    assert left == [("retention.policy.update", 200)]  # the 400-day one went with the tier
    kept = await scalar(
        engine,
        "SELECT count(*) FROM audit_log WHERE workspace_id = :w "
        "AND action = 'retention.delete.run' AND actor_id = 'u'",
        w=ws,
    )
    assert kept == 1


async def test_a_workspace_with_an_audit_period_saved_before_the_minimum_can_edit_other_tiers(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    owner = await person(api, "owner@acme.test", "OWNER")
    async with api.engine.begin() as conn:  # as 0101 allowed it: 30 days is above the old floor
        await conn.execute(
            text(
                "INSERT INTO retention_policies (workspace_id, audit_days, updated_by) "
                "VALUES (:w, 30, 'old')"
            ),
            {"w": api.tenant.context.workspace_id},
        )
    ok = await api.client.put(path(api), json={"trace": 60}, headers=owner)
    assert ok.status_code == 200, ok.text
    assert tiers(ok.json())["audit"]["days"] == 30  # shown as saved; the job applies the minimum
    bad = await api.client.put(path(api), json={"audit": 31}, headers=owner)
    assert bad.status_code == 422
