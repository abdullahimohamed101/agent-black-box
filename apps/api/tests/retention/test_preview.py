"""The retention preview (ADR-159): counts against hand-computed fixtures.

Times are fixed (`NOW`) and the fixture is small enough to count by hand; boundaries sit exactly on
the cutoff (a run that ended exactly N days ago is not yet expired) and one second past it.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from abb_api.retention import service as retention_service
from tests.api_fixtures import WEB_ORIGIN, Api, Tick, build_api, person
from tests.conftest import make_settings
from tests.retention.helpers import NOW, add_audit, days_ago, seed_run, seed_world

MakeApi = Callable[..., Awaitable[Api]]


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


@pytest.fixture
async def world(make_api: MakeApi) -> Api:
    api = await make_api()
    await seed_world(api.engine, api.tenant, api.other)
    return api


def by_tier(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {x["tier"]: x for x in body["tiers"]}


async def preview(api: Api, headers: dict[str, str], **query: str) -> Any:
    return await api.client.get(
        f"/v1/workspaces/{api.tenant.workspace_id}/retention/preview",
        params=query,
        headers=headers,
    )


async def test_the_current_policy_preview_matches_a_hand_count(world: Api) -> None:
    owner = await person(world, "owner@acme.test", "OWNER")
    response = await preview(world, owner)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["proposed"] is False and body["as_of"].startswith("2026-10-15T12:00:00")
    got = by_tier(body)
    assert [x["tier"] for x in body["tiers"]] == ["artifacts", "trace", "run", "audit"]
    # Artifacts (30 days): runs 2 (1000), 3 (10) and the second-over run (7), plus the old orphan
    # (50). Run 5 sits exactly on the cutoff and the young orphan is 29 days old: both kept.
    a = got["artifacts"]
    assert (a["runs"], a["artifact_bytes"], a["rows"], a["events"]) == (3, 1067, 4, 0)
    assert (a["days"], a["forever"], a["truncated"]) == (30, False, False)
    # Trace detail (90 days): runs 3 and 4 only. Events 5 + 2; derived rows are their spans
    # (3 + 2) and their background jobs (1 + 1).
    tr = got["trace"]
    assert (tr["runs"], tr["events"], tr["rows"], tr["artifact_bytes"]) == (2, 7, 7, 0)
    # Run record (365 days): run 4 alone.
    run = got["run"]
    assert (run["runs"], run["rows"], run["events"]) == (1, 1, 0)
    # Audit log (365 days): the entries 400 and 366 days old; 365 exactly and 364 are kept.
    au = got["audit"]
    assert (au["rows"], au["runs"], au["events"]) == (2, 0, 0)
    assert got["trace"]["cutoff"].startswith("2026-07-17T12:00:00")  # NOW - 90 days


async def test_a_proposed_policy_is_measured_without_saving_it(world: Api) -> None:
    owner = await person(world, "owner@acme.test", "OWNER")
    response = await preview(world, owner, artifacts_days="7", tiers="artifacts")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["proposed"] is True and [x["tier"] for x in body["tiers"]] == ["artifacts"]
    a = by_tier(body)["artifacts"]
    # Everything older than 7 days: runs 1 (300 in 2 files), 2, 3, 5, 6, and both orphans.
    assert (a["runs"], a["artifact_bytes"], a["rows"], a["days"]) == (5, 1432, 8, 7)
    saved = await world.client.get(
        f"/v1/workspaces/{world.tenant.workspace_id}/retention", headers=owner
    )
    assert {x["tier"]: x["days"] for x in saved.json()["tiers"]}["artifacts"] == 30
    async with world.engine.connect() as conn:
        assert (
            await conn.execute(text("SELECT count(*) FROM retention_policies"))
        ).scalar_one() == 0


async def test_the_saved_policy_is_what_a_preview_without_proposals_uses(world: Api) -> None:
    owner = await person(world, "owner@acme.test", "OWNER")
    await world.client.put(
        f"/v1/workspaces/{world.tenant.workspace_id}/retention",
        json={"trace": 200, "run": 400, "audit": 500},
        headers=owner,
    )
    got = by_tier((await preview(world, owner)).json())
    assert got["trace"]["runs"] == 1  # only run 4 (400 days) is older than 200
    assert (got["run"]["runs"], got["audit"]["rows"]) == (0, 0)  # 400 is not older than 400


async def test_a_tier_kept_forever_deletes_nothing(world: Api) -> None:
    owner = await person(world, "owner@acme.test", "OWNER")
    got = by_tier((await preview(world, owner, trace_days="forever", run_days="forever")).json())
    for tier in ("trace", "run"):
        assert (got[tier]["forever"], got[tier]["days"], got[tier]["cutoff"]) == (True, None, None)
        assert (got[tier]["runs"], got[tier]["events"], got[tier]["rows"]) == (0, 0, 0)
    assert got["artifacts"]["runs"] == 3  # the other tiers are measured as usual


async def test_workspaces_do_not_see_each_others_figures(world: Api) -> None:
    globex = await person(world, "owner@globex.test", "OWNER", workspace="globex")
    response = await world.client.get(
        f"/v1/workspaces/{world.other.workspace_id}/retention/preview", headers=globex
    )
    got = by_tier(response.json())
    assert (got["artifacts"]["runs"], got["artifacts"]["artifact_bytes"]) == (1, 9999)
    assert (got["trace"]["runs"], got["trace"]["events"], got["trace"]["rows"]) == (1, 9, 10)
    assert got["audit"]["rows"] == 1


@pytest.mark.parametrize(
    ("query", "tier", "reason"),
    [
        ({"artifacts_days": "3"}, "artifacts", "below_floor"),
        ({"artifacts_days": "100"}, "artifacts", "order"),
        ({"trace_days": "forever"}, "trace", "order"),
        ({"run_days": "99999"}, "run", "above_ceiling"),
    ],
)
async def test_an_invalid_proposal_is_refused_like_an_invalid_change(
    world: Api, query: dict[str, str], tier: str, reason: str
) -> None:
    owner = await person(world, "owner@acme.test", "OWNER")
    response = await preview(world, owner, **query)
    assert response.status_code == 422
    assert response.json()["error"]["details"] == {"tier": tier, "reason": reason}


@pytest.mark.parametrize(
    "query",
    [{"tiers": "events"}, {"tiers": "trace,,run"}, {"trace_days": "ninety"}, {"trace_days": "-5"}],
)
async def test_malformed_preview_queries_are_refused(world: Api, query: dict[str, str]) -> None:
    owner = await person(world, "owner@acme.test", "OWNER")
    assert (await preview(world, owner, **query)).status_code == 422


async def test_every_member_may_preview_and_keys_may_not(world: Api) -> None:
    for role in ("VIEWER", "BILLING", "DEVELOPER", "SECURITY", "ADMIN"):
        headers = await person(world, f"{role.lower()}@acme.test", role)
        assert (await preview(world, headers)).status_code == 200, role
    assert (
        await world.get(
            f"/v1/workspaces/{world.tenant.workspace_id}/retention/preview", token="wide_reader"
        )
    ).status_code == 403


async def test_the_bound_marks_a_lower_bound_as_truncated(make_api: MakeApi) -> None:
    api = await make_api(retention_preview_max_runs=2, retention_max_rows_per_tick=2)
    for age in (100, 110, 120, 130):
        await seed_run(api.engine, api.tenant, ended=days_ago(age), events=1, spans=0, jobs=0)
    for age in (400, 401, 402):
        await add_audit(api.engine, api.tenant.context.workspace_id, age)
    owner = await person(api, "owner@acme.test", "OWNER")
    got = by_tier((await preview(api, owner)).json())
    assert (got["trace"]["runs"], got["trace"]["events"], got["trace"]["truncated"]) == (2, 2, True)
    assert (got["audit"]["rows"], got["audit"]["truncated"]) == (2, True)
    assert got["artifacts"]["truncated"] is True  # four expired runs, two considered
    # Exactly at the bound is not truncated.
    exact = by_tier((await preview(api, owner, trace_days="130", run_days="365")).json())
    assert exact["trace"]["runs"] == 0 and exact["trace"]["truncated"] is False


async def test_a_preview_that_runs_out_of_time_is_a_504_not_a_hang(
    make_api: MakeApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = await make_api(retention_preview_timeout_seconds=0.05)

    async def slow(conn: AsyncConnection, *args: Any, **kwargs: Any) -> None:
        await conn.execute(text("SELECT pg_sleep(30)"))  # cut off by the statement timeout

    monkeypatch.setattr(retention_service, "measure_tier", slow)
    owner = await person(api, "owner@acme.test", "OWNER")
    response = await preview(api, owner)
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "RETENTION_PREVIEW_TIMEOUT"
    assert response.json()["error"]["retryable"] is True


async def test_previews_are_rate_limited_per_workspace(make_api: MakeApi) -> None:
    api = await make_api(retention_previews_per_minute=2)
    owner = await person(api, "owner@acme.test", "OWNER")
    codes = [(await preview(api, owner, tiers="audit")).status_code for _ in range(4)]
    assert codes == [200, 200, 429, 429]
    globex = await person(api, "owner@globex.test", "OWNER", workspace="globex")
    other = await api.client.get(
        f"/v1/workspaces/{api.other.workspace_id}/retention/preview", headers=globex
    )
    assert other.status_code == 200


async def test_a_preview_deletes_nothing(world: Api) -> None:
    tables = ["runs", "events", "spans", "artifacts", "audit_log", "outbox_jobs", "findings"]

    async def counts() -> dict[str, int]:
        async with world.engine.connect() as conn:
            return {
                name: (await conn.execute(text(f"SELECT count(*) FROM {name}"))).scalar_one()  # noqa: S608
                for name in tables
            }

    before = await counts()
    owner = await person(world, "owner@acme.test", "OWNER")
    await preview(world, owner)
    await preview(world, owner, artifacts_days="7", run_days="30", trace_days="20")
    after = await counts()
    for name in tables:
        if name != "audit_log":  # the person fixture may add audit rows; nothing else changes
            assert after[name] == before[name], name
    assert after["audit_log"] >= before["audit_log"]
